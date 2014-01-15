import datetime
import logging
import os
import shutil
import urlparse
import copy
import socket
import signal
import platform

from dashboard_app.models import Bundle

from django.core.files.base import ContentFile
from django.db import connection
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.utils import DatabaseError

from linaro_django_xmlrpc.models import AuthToken

import simplejson

from twisted.internet.threads import deferToThread

from zope.interface import implements

import lava_dispatcher.config as dispatcher_config

from lava_scheduler_app.models import (
    Device,
    DeviceStateTransition,
    JSONDataError,
    TestJob,
    Worker)
from lava_scheduler_app import utils
from lava_scheduler_daemon.jobsource import IJobSource

try:
    from psycopg2 import InterfaceError, OperationalError
except ImportError:
    class InterfaceError(Exception):
        pass

    class OperationalError(Exception):
        pass


class DatabaseJobSource(object):

    implements(IJobSource)

    def __init__(self):
        self.logger = logging.getLogger(__name__ + '.DatabaseJobSource')
        self.logger.debug("__init enter__")

    deferToThread = staticmethod(deferToThread)

    def deferForDB(self, func, *args, **kw):
        self.logger.debug("__deferForDB enter__")

        def wrapper(*args, **kw):
            # If there is no db connection yet on this thread, create a
            # connection and immediately commit, because rolling back the
            # first transaction on a connection loses the effect of
            # settings.TIME_ZONE when using postgres (see
            # https://code.djangoproject.com/ticket/17062).
            self.logger.debug("__wrapper enter__")
            transaction.enter_transaction_management()
            transaction.managed()
            try:
                if connection.connection is None:
                    connection.cursor().close()
                    assert connection.connection is not None
                    transaction.commit()
                try:
                    return func(*args, **kw)
                except (DatabaseError, OperationalError, InterfaceError), error:
                    message = str(error)
                    if message == 'connection already closed' or \
                            message.startswith(
                            'terminating connection due to administrator command') or \
                            message.startswith(
                            'could not connect to server: Connection refused') or \
                            message.startswith(
                            'canceling statement due to statement timeout'):
                        self.logger.warning(
                            'Forcing reconnection on next db access attempt')
                        if connection.connection:
                            if not connection.connection.closed:
                                connection.connection.close()
                            connection.connection = None
                    raise
            finally:
                # In Django 1.2, the commit_manually() etc decorators only
                # commit or rollback the transaction if Django thinks there's
                # been a write to the database.  We don't want to leave
                # transactions dangling under any circumstances so we
                # unconditionally issue a rollback.  This might be a teensy
                # bit wastful, but it wastes a lot less time than figuring out
                # why your south migration appears to have got stuck...
                transaction.rollback()
                transaction.leave_transaction_management()
        self.logger.debug("__wrapper exit__")
        self.logger.debug("__deferForDB exit__")
        return self.deferToThread(wrapper, *args, **kw)

    def _get_health_check_jobs(self):
        """Gets the list of devices and checks which are the devices that
        require health check.

        Returns JOB_LIST which is a list of health check jobs. If no health
        check jobs are available returns an empty list.
        """
        self.logger.debug("__get_health_check_jobs enter__")
        job_list = []

        for device in Device.objects.filter(heartbeat=True):
            if device.status != Device.IDLE:
                continue
            if not device.device_type.health_check_job:
                run_health_check = False
            elif device.health_status == Device.HEALTH_UNKNOWN:
                run_health_check = True
            elif device.health_status == Device.HEALTH_LOOPING:
                run_health_check = True
            elif not device.last_health_report_job:
                run_health_check = True
            else:
                if device.last_health_report_job.end_time:
                    run_health_check = \
                        device.last_health_report_job.end_time < \
                        datetime.datetime.now() - datetime.timedelta(days=1)
                else:
                    run_health_check = True
            if run_health_check:
                job = device.initiate_health_check_job()
                job.save()
                transaction.commit()
                job_list.append(job)
        self.logger.debug("__get_health_check_jobs exit__")
        return job_list

    def _release_device(self, multinode_jobs):
        """Release reserved DEVICEs from the given multinode JOB, only if
        the devices were in RESERVED state for more than 5 minutes.

        Multinode jobs which does not have all devices allocated, should
        release reserved devices held for more than 5 minutes, in order to
        avoid deadlock.
        """
        self.logger.debug("__release_device enter__")
        release_device = False

        # Determine if any one of the devices in given multinode job is held
        # RESERVED for more than 5 minutes.
        for job in multinode_jobs:
            device = None
            if job.actual_device:
                device = job.actual_device
            if device and device.status == Device.RESERVED:
                last_transition = DeviceStateTransition.objects.filter(
                    device=job.actual_device).latest('created_on')
                release_device = last_transition.created_on < \
                    datetime.datetime.now() - datetime.timedelta(minutes=5)
                if release_device:
                    break

        if release_device:
            for job in multinode_jobs:
                device = None
                if job.actual_device:
                    device = job.actual_device
                if device and device.status == Device.RESERVED:
                    self.logger.info("Releasing device %s from job %s",
                                     job.actual_device, job.id)
                    msg = "Job: %s, Release device from scheduling" % \
                        job.id
                    DeviceStateTransition.objects.create(
                        created_by=None, device=job.actual_device,
                        old_state=device.status, new_state=Device.IDLE,
                        message=msg, job=None).save()
                    device.status = Device.IDLE
                    device.current_job = None
                    job.actual_device = None
                    device.save()
                    job.save()
                    transaction.commit()
        self.logger.debug("__release_device exit__")

    def _fix_device(self, device, job):
        """Associate an available/idle DEVICE to the given JOB.

        If the MultiNode job is waiting as Submitted, the device
        could be running a different job.
        Returns the job with actual_device set to DEVICE.

        If we are unable to grab the DEVICE then we return None.
        """
        # prevent the device getting two different jobs at the same time
        self.logger.debug("__fix_device enter__")
        if job.actual_device or device.status == Device.RUNNING \
                or device.heartbeat is False or device.current_job:
            return None
        msg = "Job: %s" % job.id
        DeviceStateTransition.objects.create(
            created_by=None, device=device, old_state=device.status,
            new_state=Device.RESERVED, message=msg, job=job).save()
        device.status = Device.RESERVED
        device.current_job = job
        try:
            # The unique constraint on current_job may cause this to
            # fail in the case of concurrent requests for different
            # boards grabbing the same job.  If there are concurrent
            # requests for the *same* board they may both return the
            # same job -- this is an application level bug though.
            device.save()
        except IntegrityError:
            self.logger.info(
                "job %s has been assigned to another board -- rolling back",
                job.id)
            transaction.rollback()
            return None
        else:
            job.actual_device = device
            job.log_file.save(
                'job-%s.log' % job.id, ContentFile(''), save=False)
            job.submit_token = AuthToken.objects.create(user=job.submitter)
            job.definition = simplejson.dumps(self._get_json_data(job),
                                              sort_keys=True,
                                              indent=4 * ' ')
            job.save()
            transaction.commit()
        self.logger.debug("__fix_device exit__")
        return job

    def _delay_multinode_scheduling(self, job_list):
        """Remove scheduling multinode jobs until all the jobs in the
        target_group are assigned devices.
        """
        self.logger.debug("__delay_multinode_scheduling enter__")
        final_job_list = copy.deepcopy(job_list)
        self.logger.debug("Calculating multinode scheduling of %d jobs"
                          % len(final_job_list))
        for job in job_list:
            if job.is_multinode:
                multinode_jobs = TestJob.objects.all().filter(
                    target_group=job.target_group)
                self.logger.debug("Checking target group %s" %
                                  job.target_group)
                jobs_with_device = 0
                for multinode_job in multinode_jobs:
                    if multinode_job.actual_device:
                        self.logger.debug("actual_device %s" % multinode_job.actual_device)
                        jobs_with_device += 1
                self.logger.debug("group of size %d has %d jobs with devices"
                                  % (len(multinode_jobs), jobs_with_device))

                if len(multinode_jobs) != jobs_with_device:
                    self.logger.debug("Removing jobs from final list")
                    if utils.is_master():
                        self._release_device(multinode_jobs)
                    for m_job in multinode_jobs:
                        if m_job in final_job_list:
                            final_job_list.remove(m_job)
        self.logger.debug("Final list length: %d" % len(final_job_list))
        self.logger.debug("__delay_multinode_scheduling exit__")
        return final_job_list

    def _process_multinode_jobs(self, job):
        self.logger.debug("__process_multinode_jobs enter__")
        job_list = []
        if job.is_multinode:
            multinode_jobs = TestJob.objects.all().filter(
                target_group=job.target_group)

            for multinode_job in multinode_jobs:
                devices = []
                self.logger.debug("Checking devices of requested type %s owned by %s" %
                                  (multinode_job.requested_device_type, multinode_job.submitter.username))
                device_list = Device.objects.all().filter(
                    device_type=multinode_job.requested_device_type,
                    status=Device.IDLE, heartbeat=True, is_public=False)
                for d in device_list:
                    if d.can_submit(multinode_job.submitter):
                        devices.append(d)
                if len(devices) == 0:
                    self.logger.debug("Checking public devices of requested type %s" %
                                      multinode_job.requested_device_type)
                    devices = Device.objects.all().filter(
                        device_type=multinode_job.requested_device_type,
                        status=Device.IDLE, heartbeat=True, is_public=True)
                if len(devices) > 0:
                    f_job = self._fix_device(devices[0], multinode_job)
                    if f_job:
                        job_list.append(f_job)

        self.logger.debug("__process_multinode_jobs exit__")
        return job_list

    def _assign_jobs(self, jobs):
        """
        uses job.submitter to check owned devices first before public devices

        CAUTION: devices is a list but only one device can be allowed to
        be assigned to any one job and only one job can run on any one device.

        _fix_device rejects assignments if the job has an actual_device or
        if the device has already transitioned to Reserved or Running.

        :param jobs: JSON string of the job request
        :return: a list of jobs with devices to reserve
        """
        self.logger.debug("__assign_jobs enter__")
        job_list = self._get_health_check_jobs()
        devices = []

        for job in jobs:
            if job.is_multinode and not job.actual_device:
                job_list = job_list + self._process_multinode_jobs(job)
            else:
                if job.actual_device:
                    job_list.append(job)
                elif job.requested_device:
                    self.logger.debug("Checking if requested device %s is owned by %s" %
                                      (job.requested_device.hostname, job.submitter.username))
                    # important: the result of any filter is always a list
                    device_list = Device.objects.all().filter(
                        hostname=job.requested_device.hostname,
                        status=Device.IDLE,
                        heartbeat=True)
                    for d in device_list:
                        if d.can_submit(job.submitter):
                            devices.append(d)
                elif job.requested_device_type:
                    self.logger.debug("Checking devices of requested type %s owned by %s" %
                                      (job.requested_device_type, job.submitter.username))
                    device_list = Device.objects.all().filter(
                        device_type=job.requested_device_type,
                        status=Device.IDLE, heartbeat=True, is_public=False)
                    for d in device_list:
                        if d.can_submit(job.submitter):
                            devices.append(d)
                    if len(devices) == 0:
                        # only check public devices if no restricted devices are available.
                        self.logger.debug("Checking public devices of requested type %s" %
                                          job.requested_device_type)
                        devices = list(Device.objects.all().filter(
                            device_type=job.requested_device_type,
                            status=Device.IDLE, heartbeat=True, is_public=True))
                else:
                    continue
                if len(devices) > 0:
                    for d in devices:
                        if job:
                            job = self._fix_device(d, job)
                        if job:
                            job_list.append(job)

        self.logger.debug("__assign_jobs exit__")
        return job_list

    def _worker_host(self, worker_name):
        self.logger.debug("__worker_host enter__")
        worker = None
        worker_info = {}
        worker_info['hostname'] = worker_name
        worker_info['uptime'] = utils.get_uptime()

        try:
            worker = Worker.objects.get(hostname=worker_name)
            if worker:
                worker.uptime = worker_info['uptime']
                worker.last_heartbeat = datetime.datetime.utcnow()
                worker.save()
                transaction.commit()
                self.logger.debug("Heartbeat timestamp updated for %s ..." %
                                  worker.hostname)
        except:
            # Fill information
            worker_info['hardware_info'] = utils.get_lshw_out()
            worker_info['description'] = None
            worker_info['arch'] = platform.machine()
            worker_info['platform'] = platform.platform()
            worker_info['last_heartbeat'] = datetime.datetime.utcnow()
            worker = Worker.objects.create(**worker_info)
            worker.save()
            transaction.commit()
            self.logger.debug("Worker Host %s added ..." % worker.hostname)
        self.logger.debug("__worker_host exit__")
        return worker

    def _device_heartbeat(self):
        """LAST_HEARTBEAT and WORKER_HOSTNAME fields gets updated for each
        configured device, which is not RETIRED.
        """
        self.logger.debug("__device_heartbeat enter__")
        devices = Device.objects.all()
        configured_boards = [
            x.hostname for x in dispatcher_config.get_devices()]
        for device in devices:
            if device.hostname in configured_boards and \
                    device.status is not Device.RETIRED:
                device.worker_host = self._worker_host(socket.getfqdn())
                device.last_heartbeat = datetime.datetime.utcnow()
                device.save()
                transaction.commit()
                self.logger.debug("Heartbeat timestamp updated for %s ..." %
                                  device.hostname)
        self.logger.debug("__device_heartbeat exit__")

    def _update_heartbeat(self):
        """Update HEARTBEAT based on LAST_HEARTBEAT timestamp."""
        self.logger.debug("__update_heartbeat enter__")
        devices = Device.objects.all()
        for device in devices:
            device.too_long_since_last_heartbeat()
            transaction.commit()
            self.logger.debug(
                "Device heartbeat updated for %s ..." % device.hostname)
        workers = Worker.objects.all()
        for worker in workers:
            worker.too_long_since_last_heartbeat()
            transaction.commit()
            self.logger.debug(
                "Worker heartbeat updated for %s ..." % worker.hostname)
        self.logger.debug("__device_heartbeat exit__")

    def _kill_canceling(self, job):
        """
        Kills any remaining lava-dispatch processes via the pgid in the jobpid file

        :param job: the TestJob stuck in Canceling
        """
        self.logger.debug("__kill_canceling enter__")
        pidrecord = os.path.join(job.output_dir, "jobpid")
        if os.path.exists(pidrecord):
            with open(pidrecord, 'r') as f:
                pgid = int(f.read())
                self.logger.info("Signalling SIGTERM to process group: %d" % pgid)
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except OSError as e:
                    self.logger.info("Unable to kill process group %d: %s" % (pgid, e))
                    os.unlink(pidrecord)
        self.logger.debug("__kill_canceling exit__")

    def _cleanup_device_status(self):
        """Pick up each device in the database and ensure their status is
        properly recorded, based on the job that has run on the device
        previously.

        NOTE: This is required in situations where the SchedulerMonitor has
        lost connection via twisted and the devices remain in deadlocked state.

        This should run only on the master scheduler.
        """
        self.logger.debug("__cleanup_device_status enter__")
        devices = Device.objects.all()
        for device in devices:
            save_device = False
            self.logger.debug('ensuring proper device state on %s', device)
            old_device_status = device.status
            if device.status == Device.RUNNING:
                device.status = Device.IDLE
            elif device.status == Device.OFFLINING:
                device.status = Device.OFFLINE
            elif device.status == Device.RESERVED:
                self.logger.debug("checking reserved device %s" % device.current_job)
                if device.current_job and device.current_job.status in [TestJob.RUNNING]:
                    device.status = Device.RUNNING
                    save_device = True
                elif device.current_job and device.current_job.status in [TestJob.COMPLETE]:
                    device.status = Device.IDLE
                    save_device = True
                elif device.current_job is None:
                    self.logger.error("How did we get a reserved device with no job?")
                    device.status = Device.IDLE
                    save_device = True
            else:
                continue
            job = device.current_job
            if job:
                if job.status in [TestJob.COMPLETE, TestJob.INCOMPLETE,
                                  TestJob.CANCELED]:
                    device.device_version = _get_device_version(
                        job.results_bundle)
                    device.current_job = None
                    msg = "Job: %s" % job.id
                    DeviceStateTransition.objects.create(
                        created_by=None, device=device,
                        old_state=old_device_status, new_state=device.status,
                        message=msg, job=job).save()
                    save_device = True

                if job.health_check and job.status not in [TestJob.RUNNING,
                                                           TestJob.CANCELING]:
                    device.last_health_report_job = job
                    if device.health_status != Device.HEALTH_LOOPING:
                        if job.status == TestJob.INCOMPLETE:
                            device.health_status = Device.HEALTH_FAIL
                            device.put_into_maintenance_mode(None, "Health Check Job Failed")
                        elif job.status == TestJob.COMPLETE:
                            device.health_status = Device.HEALTH_PASS
                        save_device = True

            if save_device:
                device.save()
                transaction.commit()
        self.logger.debug("__cleanup_device_status enter__")

    def getJobList_impl(self):
        self.logger.debug("__getJobList_impl enter__")
        self._device_heartbeat()

        job_list = TestJob.objects.all().filter(
            status=TestJob.SUBMITTED).order_by('-health_check', '-priority',
                                               'submit_time')

        cancel_list = TestJob.objects.all().filter(status=TestJob.CANCELING)
        # Pick up TestJob objects in Canceling and ensure that the cancel completes.
        # call _kill_canceling to terminate any lava-dispatch calls
        # Explicitly set a DeviceStatusTransition as jobs which are stuck in Canceling
        #  may already have lost connection to the SchedulerMonitor via twisted.
        # Call TestJob.cancel to reset the TestJob status
        if len(cancel_list) > 0:
            self.logger.debug("Number of jobs in cancelling status %d" % len(cancel_list))
            for job in cancel_list:
                # Check that a job can only be cancelled by the worker which the device resides.
                if job.actual_device and job.actual_device.worker_host.hostname == socket.getfqdn():
                    self.logger.debug("Looking for pid of dispatch job %s in %s" % (job.id, job.output_dir))
                    self._kill_canceling(job)
                    device = Device.objects.get(hostname=job.actual_device.hostname)
                    if device.status == Device.RUNNING:
                        self.logger.info("Transitioning %s to Idle" % device.hostname)
                        device.current_job = None
                        device.status = Device.IDLE
                        device.save()
                        msg = "Worker %s cancelled job: %s" % (job.actual_device.worker_host.hostname, job.id)
                        DeviceStateTransition.objects.create(
                            created_by=None, device=device, old_state=Device.RUNNING,
                            new_state=Device.IDLE, message=msg, job=job).save()
                    self.logger.debug('Marking job %s as cancelled on %s' % (job.id, job.actual_device))
                    job.cancel()
                    transaction.commit()

        if utils.is_master():
            self._cleanup_device_status()
            self._update_heartbeat()
            self.logger.debug("Boards assigned to jobs ...")
            job_list = self._assign_jobs(job_list)
        self.logger.debug("Job list returned ...")
        self.logger.debug("__getJobList_impl exit__")
        return self._delay_multinode_scheduling([job for job in job_list])

    def getJobList(self):
        self.logger.debug("__getJobList enter__")
        self.logger.debug("__getJobList exit__")
        return self.deferForDB(self.getJobList_impl)

    def _get_json_data(self, job):
        self.logger.debug("__get_json_data enter__")
        json_data = simplejson.loads(job.definition)
        if job.actual_device:
            json_data['target'] = job.actual_device.hostname
        elif job.requested_device:
            json_data['target'] = job.requested_device.hostname
        for action in json_data['actions']:
            if not action['command'].startswith('submit_results'):
                continue
            params = action['parameters']
            params['token'] = job.submit_token.secret
            parsed = urlparse.urlsplit(params['server'])
            netloc = job.submitter.username + '@' + parsed.hostname
            if parsed.port:
                netloc += ':' + str(parsed.port)
            parsed = list(parsed)
            parsed[1] = netloc
            params['server'] = urlparse.urlunsplit(parsed)
        json_data['health_check'] = job.health_check
        self.logger.debug("__get_json_data exit__")
        return json_data

    def getJobDetails_impl(self, job):
        self.logger.debug("__getJobDetails_impl enter__")
        job.status = TestJob.RUNNING
        # need to set the device RUNNING if device was RESERVED
        if job.actual_device.status == Device.RESERVED:
            msg = "Job: %s" % job.id
            DeviceStateTransition.objects.create(
                created_by=None, device=job.actual_device, old_state=job.actual_device.status,
                new_state=Device.RUNNING, message=msg, job=job).save()
            job.actual_device.status = Device.RUNNING
            job.actual_device.current_job = job
            job.actual_device.save()
        job.start_time = datetime.datetime.utcnow()
        shutil.rmtree(job.output_dir, ignore_errors=True)
        job.log_file.save('job-%s.log' % job.id, ContentFile(''), save=False)
        job.save()
        json_data = self._get_json_data(job)
        transaction.commit()
        self.logger.debug("__getJobDetails_impl exit__")
        return json_data

    def getJobDetails(self, job):
        self.logger.debug("__getJobDetails enter__")
        self.logger.debug("__getJobDetails exit__")
        return self.deferForDB(self.getJobDetails_impl, job)

    def getOutputDirForJobOnBoard_impl(self, board_name):
        self.logger.debug("__getOutputDirForJobOnBoard_impl enter__")
        device = Device.objects.get(hostname=board_name)
        job = device.current_job
        self.logger.debug("__getOutputDirForJobOnBoard_impl exit__")
        return job.output_dir

    def getOutputDirForJobOnBoard(self, board_name):
        self.logger.debug("__getOutputDirForJobOnBoard enter__")
        self.logger.debug("__getOutputDirForJobOnBoard exit__")
        return self.deferForDB(self.getOutputDirForJobOnBoard_impl, board_name)

    def jobCompleted_impl(self, board_name, exit_code, kill_reason):
        self.logger.debug("__jobCompleted_impl enter__")
        self.logger.debug('marking job as complete on %s', board_name)
        device = Device.objects.get(hostname=board_name)
        old_device_status = device.status
        if device.status == Device.RUNNING:
            device.status = Device.IDLE
        elif device.status == Device.OFFLINING:
            device.status = Device.OFFLINE
        elif device.status == Device.RESERVED:
            device.status = Device.IDLE
        elif device.status == Device.IDLE:
            pass  # Do nothing, since _cleanup_device_status made this change.
        else:
            self.logger.error(
                "Unexpected device state in jobCompleted: %s" % device.status)
            device.status = Device.IDLE
        job = device.current_job
        device.device_version = _get_device_version(job.results_bundle)
        device.current_job = None
        if job.status == TestJob.RUNNING:
            if exit_code == 0:
                job.status = TestJob.COMPLETE
            else:
                job.status = TestJob.INCOMPLETE
        elif job.status == TestJob.CANCELING:
            job.status = TestJob.CANCELED
        else:
            self.logger.error(
                "Unexpected job state in jobCompleted: %s" % job.status)
            job.status = TestJob.COMPLETE
        msg = "Job: %s" % job.id
        DeviceStateTransition.objects.create(
            created_by=None, device=device, old_state=old_device_status,
            new_state=device.status, message=msg, job=job).save()

        if job.health_check:
            device.last_health_report_job = job
            if device.health_status != Device.HEALTH_LOOPING:
                if job.status == TestJob.INCOMPLETE:
                    device.health_status = Device.HEALTH_FAIL
                    device.put_into_maintenance_mode(None, "Health Check Job Failed")
                elif job.status == TestJob.COMPLETE:
                    device.health_status = Device.HEALTH_PASS

        bundle_file = os.path.join(job.output_dir, 'result-bundle')
        if os.path.exists(bundle_file):
            with open(bundle_file) as f:
                results_link = f.read().strip()
            job._results_link = results_link
            sha1 = results_link.strip('/').split('/')[-1]
            try:
                bundle = Bundle.objects.get(content_sha1=sha1)
            except Bundle.DoesNotExist:
                pass
            else:
                job._results_bundle = bundle

        job.end_time = datetime.datetime.utcnow()
        token = job.submit_token
        job.submit_token = None
        device.save()
        job.save()
        token.delete()
        try:
            job.send_summary_mails()
        except:
            # Better to catch all exceptions here and log it than have this
            # method fail.
            self.logger.exception(
                'sending job summary mails for job %r failed', job.pk)
        self.logger.debug("__jobCompleted_impl exit__")
        transaction.commit()

    def jobCompleted(self, board_name, exit_code, kill_reason):
        self.logger.debug("__jobCompleted enter__")
        self.logger.debug("__jobCompleted exit__")
        return self.deferForDB(self.jobCompleted_impl, board_name, exit_code, kill_reason)

    def jobCheckForCancellation_impl(self, board_name):
        self.logger.debug("__jobCheckForCancellation_impl enter__")
        device = Device.objects.get(hostname=board_name)
        job = device.current_job
        self.logger.debug("__jobCheckForCancellation_impl exit__")
        return job.status != TestJob.RUNNING

    def jobCheckForCancellation(self, board_name):
        self.logger.debug("_jobCheckForCancellation enter__")
        self.logger.debug("_jobCheckForCancellation exit__")
        return self.deferForDB(self.jobCheckForCancellation_impl, board_name)


def _get_device_version(bundle):
    if bundle is None:
        return None
    try:
        lava_test_run = bundle.test_runs.filter(test__test_id='lava')[0]
        version_attribute = lava_test_run.attributes.filter(name='target.device_version')[0]
        return version_attribute.value
    except IndexError:
        return 'unknown'
