.. _dispatcher_design:

Lava Dispatcher Design
**********************

The new dispatcher design is intended to make it easier to adapt the
dispatcher flow to new boards, new mechanisms and new deployments.

.. note:: The new code is still developing, some areas are absent,
          some areas will change substantially before it will work.
          All details here need to be seen only as examples and the
          specific code may well change independently.

Start with a Job which is broken up into a Deployment, a Boot, a Test
and a Submit class:

+-------------+--------------------+------------------+-------------------+
|     Job     |                    |                  |                   |
+=============+====================+==================+===================+
|             |     Deployment     |                  |                   |
+-------------+--------------------+------------------+-------------------+
|             |                    |   DeployAction   |                   |
+-------------+--------------------+------------------+-------------------+
|             |                    |                  |  DownloadAction   |
+-------------+--------------------+------------------+-------------------+
|             |                    |                  |  ChecksumAction   |
+-------------+--------------------+------------------+-------------------+
|             |                    |                  |  MountAction      |
+-------------+--------------------+------------------+-------------------+
|             |                    |                  |  CustomiseAction  |
+-------------+--------------------+------------------+-------------------+
|             |                    |                  |  TestDefAction    |
+-------------+--------------------+------------------+-------------------+
|             |                    |                  |  UnmountAction    |
+-------------+--------------------+------------------+-------------------+
|             |                    |   BootAction     |                   |
+-------------+--------------------+------------------+-------------------+
|             |                    |   TestAction     |                   |
+-------------+--------------------+------------------+-------------------+
|             |                    |   SubmitAction   |                   |
+-------------+--------------------+------------------+-------------------+

The Job manages the Actions using a Pipeline structure. Actions
can specialise actions by using internal pipelines and an Action
can include support for retries and other logical functions:

+------------------------+----------------------------+
|     DownloadAction     |                            |
+========================+============================+
|                        |    HttpDownloadAction      |
+------------------------+----------------------------+
|                        |    FileDownloadAction      |
+------------------------+----------------------------+

If a Job includes one or more Test definitions, the Deployment can then
extend the Deployment to overlay the LAVA test scripts without needing
to mount the image twice:

+----------------------+------------------+---------------------------+
|     DeployAction     |                  |                           |
+======================+==================+===========================+
|                      |   OverlayAction  |                           |
+----------------------+------------------+---------------------------+
|                      |                  |   MultinodeOverlayAction  |
+----------------------+------------------+---------------------------+
|                      |                  |   LMPOverlayAction        |
+----------------------+------------------+---------------------------+

The TestDefinitionAction has a similar structure with specialist tasks
being handed off to cope with particular tools:

+--------------------------------+-----------------+-------------------+
|     TestDefinitionAction       |                 |                   |
+================================+=================+===================+
|                                |    RepoAction   |                   |
+--------------------------------+-----------------+-------------------+
|                                |                 |   GitRepoAction   |
+--------------------------------+-----------------+-------------------+
|                                |                 |   BzrRepoAction   |
+--------------------------------+-----------------+-------------------+
|                                |                 |   TarRepoAction   |
+--------------------------------+-----------------+-------------------+
|                                |                 |   UrlRepoAction   |
+--------------------------------+-----------------+-------------------+

.. _code_flow:

Following the code flow
=======================

+------------------------------------------+-------------------------------------------------+
|                Filename                  |   Role                                          |
+==========================================+=================================================+
| lava/dispatcher/commands.py              | Command line arguments, call to YAML parser     |
+------------------------------------------+-------------------------------------------------+
| lava_dispatcher/pipeline/device.py       | YAML Parser to create the Device object         |
+------------------------------------------+-------------------------------------------------+
| lava_dispatcher/pipeline/parser.py       | YAML Parser to create the Job object            |
+------------------------------------------+-------------------------------------------------+
| ....pipeline/actions/deploy/             | Handlers for different deployment strategies    |
+------------------------------------------+-------------------------------------------------+
| ....pipeline/actions/boot/               | Handlers for different boot strategies          |
+------------------------------------------+-------------------------------------------------+
| ....pipeline/actions/test/               | Handlers for different LavaTestShell strategies |
+------------------------------------------+-------------------------------------------------+
| ....pipeline/actions/deploy/image.py     | DeployImage strategy creates DeployImageAction  |
+------------------------------------------+-------------------------------------------------+
| ....pipeline/actions/deploy/image.py     | DeployImageAction.populate adds deployment      |
|                                          | actions to the Job pipeline                     |
+------------------------------------------+-------------------------------------------------+
|   ***repeat for each strategy***         | each ``populate`` function adds more Actions    |
+------------------------------------------+-------------------------------------------------+
| ....pipeline/action.py                   | ``Pipeline.run_actions()`` to start             |
+------------------------------------------+-------------------------------------------------+

The deployment is determined from the device_type specified in the Job
(or the device_type of the specified target) by reading the list of
support methods from the device_types YAML configuration.

Each Action can define an internal pipeline and add sub-actions in the
``Action.populate`` function.

Particular Logic Actions (like RetryAction) require an internal pipeline
so that all actions added to that pipeline can be retried in the same
order. (Remember that actions must be idempotent.) Actions which fail
with a JobError or InfrastructureError can trigger Diagnostic actions.
See :ref:`retry_diagnostic`.

.. code-block:: yaml

 actions:
   deploy:
     allow:
       - image
   boot:
     allow:
       - image

This then matches the python class structure::

 actions/
    deploy/
        image.py

The class defines the list of Action classes needed to implement this
deployment. See also :ref:`dispatcher_actions`.

.. _pipeline_construction:

Pipeline construction and flow
==============================

#. One device per job. One top level pipeline per job

   * loads only the configuration required for this one job.

#. A NewDevice is built from the target specified (commands.py)
#. A Job is generated from the YAML by the parser.
#. The top level Pipeline is constructed by the parser.
#. Strategy classes are initialised by the parser

   #. Strategy classes add the top level Action for that strategy to the
      top level pipeline.
   #. Top level pipeline calls ``populate()`` on each top level Action added.

      #. Each ``Action.populate()`` function may construct one internal
         pipeline, based on parameters.
      #. internal pipelines call ``populate()`` on each Action added.

#. Parser iterates over each Strategy
#. Parser adds the FinalizeAction to the top-level pipeline
#. Loghandlers are set up
#. Job validates the completed pipeline

   #. Dynamic data can be added to the context

#. If ``--validate`` not specified, the job runs.

   #. Each ``run()`` function can add dynamic data to the context and/or
      results to the pipeline.
   #. Pipeline iterates through actions

#. Job ends, check for errors
#. Completed pipeline is available.

.. _using_strategy_classes:

Using strategy classes
----------------------

Strategies are ways of meeting the requirements of the submitted job within
the limits of available devices and code support.

If an internal pipeline would need to allow for optional actions, those
actions still need to be idempotent. Therefore, the pipeline can include
all actions, with each action being responsible for checking whether
anything actually needs to be done. The populate function should avoid
using conditionals. An explicit select function can be used instead.

Whenever there is a need for a particular job to use a different Action
based solely on job parameters or device configuration, that decision
should occur in the Strategy selection using classmethod support.

Where a class is used in lots of different strategies, identify whether
there is a match between particular strategies always needing particular
options within the class. At this point, the class can be split and
particular strategies use a specialised class implementing the optional
behaviour and calling down to the base class for the rest.

If there is no clear match, for example in ``testdef.py`` where any
particular job could use a different VCS or URL without actually being
a different strategy, a select function is preferable. A select handler
allows the pipeline to contain only classes supporting git repositories
when only git repositories are in use for that job.

The list of available strategies can be determined in the codebase from
the module imports in the ``strategies.py`` file for each action type.

This results in more classes but a cleaner (and more predictable)
pipeline construction.

Lava test shell scripts
=======================

.. note:: See :ref:`criteria` - it is a mistake to think of the LAVA
          test support scripts as an *overlay* - the scripts are an
          **extension** to the test. Wherever possible, current
          deployments are being changed to supply the extensions
          alongside the deployment instead of overlaying, and thereby
          altering, the deployment.

The LAVA scripts a standard addition to a LAVA test and are handled as
a single unit. Using idempotent actions, the test script extension can
support LMP or MultiNode or other custom requirements without requiring
this support to be added to all tests. The extensions are created during
the deploy strategy and specific deployments can override the
``ApplyExtensionAction`` to unpack the extension tarball alongside the
test during the deployment phase and then mount the extension inside the
image. The tarball itself remains in the output directory and becomes
part of the test records. The checksum of the overlay is added to the
test job log.

Pipeline error handling
=======================

.. _runtime_error_exception:

RuntimeError Exception
----------------------

Runtime errors include:

#. Parser fails to handle device configuration
#. Parser fails to handle submission YAML
#. Parser fails to locate a Strategy class for the Job.
#. Code errors in Action classes cause Pipeline to fail.
#. Errors in YAML cause errors upon pipeline validation.

Each runtime error is a bug in the code - wherever possible, implement
a unit test to prevent regressions.

.. _infrastructure_error_exception:

InfrastructureError Exception
-----------------------------

Infrastructure errors include:

#. Missing dependencies on the dispatcher
#. Device configuration errors

.. _job_error_exception:

JobError Exception
------------------

Job errors include:

#. Failed to find the specified URL.
#. Failed in an operation to create the necessary extensions.

.. _test_error_exception:

TestError Exception
-------------------

Test errors include:

#. Failed to handle a signal generated by the device
#. Failed to parse a test case

Result bundle identifiers
=========================

Old style result bundles are assigned a text based UUID during submission.
This has several issues:

* The UUID is not sequential or predictable, so finding this one, the
  next one or the previous one requires a database lookup for each. The
  new dispatcher model will not have a persistent database connection.
* The UUID is not available to the dispatcher whilst running the job, so
  cannot be cross-referenced to logs inside the job.
* The UUID makes the final URL of individual test results overly long,
  unmemorable and complex, especially as the test run is also given
  a separate UUID in the old dispatcher model.

The new dispatcher creates a pipeline where every action within the
pipeline is guaranteed to have a unique *level* string which is strictly
sequential, related directly to the type of action and shorter than a
UUID. To make a pipeline result unique on a per instance basis, the only
requirement is that the result includes the JobID which is a sequential
number, passed to the job in the submission YAML. This could also have
been a UUID but the JobID is already a unique ID **for this instance**.

When bundles are downloaded, the database query will need to assign a
UUID to that downloaded file but the file will also include the job
number and the query can also insert the source of the bundle in a
comment in the YAML. This will allow bundles to be uploaded to a different
instance using ``lava-tool`` without the risk of collisions. It is also
possible that the results could provide a link back to the original
job log file and other data - if the original server is visible to
users of the server to which the bundle was later uploaded.

.. _criteria:

Refactoring review criteria
===========================

The refactored dispatcher has different objectives to the original and
any assumptions in the old code must be thrown out. It is very easy to
fall into the old way of writing dispatcher code, so these criteria are
to help developers control the development of new code. Any of these
criteria can be cited in a code review as reasons for a review to be
improved.

.. _keep_dispatcher_dumb:

Keep the dispatcher dumb
------------------------

There is a temptation to make the dispatcher clever but this only
restricts the test writer from doing their own clever tests by hard
coding commands into the dispatcher codebase. If the dispatcher needs
some information about the test image, that information **must** be
retrieved from the job submission parameters, **not** by calculating
in the dispatcher or running commands inside the test image. Exceptions
to this are the metrics already calculated during download, like file
size and checksums. Any information about the test image which is
permanent within that image, e.g. the partition UUID strings or the
network interface list, can be identified by the process creating that
image or by a script which is run before the image is compressed and
made available for testing. If a test uses a tarball instead of an image,
the test **must** be explicit about the filesystem to use when
unpacking that tarball for use in the test as well as the size and
location of the partition to use.

LAVA will need to implement some safeguards for tests which still need
to deploy any test data to the media hosting the bootloader (e.g. fastboot,
SD card or UEFI) in order to avoid overwriting the bootloader itself.
Therefore, although SD card partitions remain available for LAVA tests
where no other media are supportable by the device, those tests can
**only** use tarballs and pre-defined partitions on the SD card. The
filesystem to use on those partitions needs to be specified by the test
writer.

.. _defaults:

Avoid defaults in dispatcher code
---------------------------------

Constants and defaults are going to need an override somewhere for some
device or test, eventually. Code defensively and put constants into
the utilities module to support modification. Put defaults into the
YAML, not the python code. It is better to have an extra line in the
device_type than a string in the python code as this can later be
extended to a device or a job submission.

Let the test fail and diagnose later
------------------------------------

**Avoid guessing** in LAVA code. If any operation in the dispatcher
could go in multiple paths, those paths must be made explicit to the
test writer. Report the available data, proceed according to the job
definition and diagnose the state of the device afterwards, where
appropriate.

**Avoid trying to be helpful in the test image**. Anticipating an error
and trying to code around it is a mistake. Possible solutions include
but are not limited to:

* Provide an optional, idempotent, class which only acts if a specific
  option is passed in the job definition. e.g. AutoLoginAction.
* Provide a diagnostic class which triggers if the expected problem
  arises. Report on the actual device state and document how to improve
  the job submission to avoid the problem in future.
* Split the deployment strategy to explicitly code for each possible
  path.

AutoLogin is a good example of the problem here. For too long, LAVA has
made assumptions about the incoming image, requiring hacks like
``linaro-overlay`` packages to be added to basic bootstrap images or
disabling passwords for the root user. These *helpful* steps act to
make it harder to use unchanged third party images in LAVA tests.
AutoLogin is the *de facto* default for non-Linaro images.

Another example is the assumption in various parts of LAVA that the
test image will raise a network interface and repeatedly calling ``ping``
on the assumption that the interface will appear, somehow, eventually.

.. _black_box_deploy:

Treat the deployment as a black box
-----------------------------------

LAVA has claimed to do this for a long time but the refactored
dispatcher is pushing this further. Do not think of the LAVA scripts
as an *overlay*, the LAVA scripts are **extensions**. When a test wants
an image deployed, the LAVA extensions should be deployed alongside the
image and then mounted to create a ``/lava-$hostname/`` directory. Images
for testing within LAVA are no longer broken up or redeployed but **must**
be deployed **intact**. This avoids LAVA needing to know anything about
issues like SELinux or specific filesystems but may involve multiple
images for systems like Android where data may exist on different physical
devices.

.. _essential_components:

Only protect the essential components
-------------------------------------

LAVA has had a tendency to hardcode commands and operations and there
are critical areas which must still be protected from changes in the
test but these critical areas are restricted to:

#. The dispatcher.
#. Unbricking devices.

**Any** process which has to run on the dispatcher itself **must** be
fully protected from mistakes within tests. This means that **all**
commands to be executed by the dispatcher are hardcoded into the dispatcher
python code with only limited support for overriding parameters or
specifying *tainted* user data.

Tests are prevented from requiring new software to be installed on any
dispatcher which is not already a dependency of ``lava-dispatcher``.
Issues arising from this need to be resolved using MultiNode.

Until such time as there is a general and reliable method of deploying
and testing new bootloaders within LAVA tests, the bootloader / firmware
installed by the lab admin is deemed sacrosanct and must not be altered
or replaced in a test job. However, bootloaders are generally resilient
to errors in the commands, so the commands given to the bootloader remain
accessible to test writers.

It is not practical to scan all test definitions for potentially harmful
commands. If a test inadvertently corrupts the SD card in such a way that
the bootloader is corrupted, that is an issue for the lab admins to
take up with the test submitter.

Give the test writer enough rope
--------------------------------

Within the provisos of :ref:`essential_components`, the test writer
needs to be given enough rope and then let LAVA **diagnose** issues
after the event.

There is no reason to restrict the test writer to using LAVA commands
inside the test image - as long as the essential components remain
protected.

Examples:

#. KVM devices need to protect the QEMU command line because these
   commands run on the dispatcher
#. VM devices running on an arndale do **not** need the command line
   to be coded within LAVA. There have already been bug reports on this
   issue.

:ref:`diagnostic_actions` report on the state of the device after some
kind of error. This reporting can include:

* The presence or absence of expected files (like ``/dev/disk/by-id/``
  or ``/proc/net/pnp``).
* Data about running processes or interfaces, e.g. ``ifconfig``

It is a mistake to attempt to calculate data about a test image - instead,
require that the information is provided and **diagnose** the actual
information if the attempt to use the specified information fails.

Guidance
^^^^^^^^

#. If the command is to run inside a deployment, **require** that the
   **full** command line can be specified by the test writer. Remember:
   :ref:`defaults`. It is recommended to have default commands where
   appropriate but these defaults need to support overrides in the job
   submission. This includes using a locally built binary instead of an
   executable installed in ``/usr/bin`` or similar.
#. If the command is run on a dispatcher, **require** that the binary
   to be run on the dispatcher is actually installed on the dispatcher.
   If ``/usr/bin/git`` does not exist, this is a validation error. There
   should be no circumstances where a tool required on the dispatcher
   cannot be identified during validation of the pipeline.
#. An error from running the command on the dispatcher with user-specified
   parameters is a JobError.
#. Where it is safe to do so, offer **overrides** for supportable
   commandline options.

The codebase itself will help identify how much control is handed over
to the test writer. ``self.run_command()`` is a dispatcher call and
needs to be protected. ``connection.sendline()`` is a deployment
call and does not need to be protected.

Providing gold standard images
------------------------------

Test writers are strongly recommended to only use a known working
setup for their job. A set of gold standard jobs will be defined in
association with the QA team. These jobs will provide a known baseline
for test definition writers, in a similar manner as the existing QA test
definitions provide a base for more elaborate testing.

There will be a series of images provided for as many device types as
practical, covering the basic deployments. Test definitions will be
required to be run against these images before the LAVA team will spend
time investigating bugs arising from tests. These images will provide a
measure of reassurance around the following issues:

* Kernel fails to load NFS or ramdisk.
* Kernel panics when asked to use secondary media.
* Image containing a different kernel to the gold standard fails
  to deploy.

.. note:: It is imperative that test writers understand that a gold
          standard deployment for one device type is not necessarily
          supported for a second device type. Some devices will
          never be able to support all deployment methods due to
          hardware constraints or the lack of kernel support. This is
          **not** a bug in LAVA.
          If a particular deployment is supported but not stable on a
          device type, there will not be a gold standard image for that
          deployment. Any issues in the images using such deployments
          on that type are entirely down to the test writer to fix.

The refactoring will provide :ref:`diagnostic_actions` which point at
these issues and recommend that the test is retried using the standard
kernel, dtb, initramfs, rootfs and other components.

The reason to give developers enough rope is precisely so that kernel
developers are able to fix issues in the test images before problems
show up in the gold standard images. Test writers need to work with the
QA team, using the gold standard images.

Creating a gold standard image
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Part of the benefit of a standard image is that the methods for building
the image - and therefore the methods for updating it, modifying it and
preparing custom images based upon it - must be documented clearly.

Where possible, standard tools familiar to developers of the OS concerned
should be used, e.g. debootstrap for Debian based images. The image can
also be a standard OS install. Gold standard images are not "Linaro"
images and should not require Linaro tools. Use AutoLogin support where
required instead of modifying existing images to add Linaro-specific
tools.

All gold standard images need to be kept up to date with the base OS as
many tests will want to install extra software on top and it will waste
time during the test if a lot of other packages need to be updated at
the same time. An update of a gold standard image still needs to be
tested for equivalent or improved performance compared to the current
image before replacing it.

The documentation for building and updating the image needs to be
provided alongside the image itself as a README. This text file should
also be reproduced on a wiki page and contain a link to that page. Any
wiki can be used - if a suitable page does not already exist elsewhere,
use wiki.linaro.org.

Other gold standard components
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The standard does not have to be a complete OS image - a kernel with a
DTB (and possibly an initrd) can also count as a standard ramdisk image.
Similarly, a combination of kernel and rootfs can count as a standard
NFS configuration.

The same requirement exists for documenting how to build, modify and
update all components of the "image" and the set of components need to
be tested as a whole to represent a test using the standard.

Secondary media
===============

With the migration from master images on an SD card to dynamic master
images over NFS, other possibilities arise from the refactoring.

* Deploy a ramdisk, boot and deploy an entire image to a USB key, boot
  and direct bootloader at USB filesystem, including kernel and initrd.
* Deploy an NFS system, boot and bootstrap an image to SATA, boot and
  direct bootloader at SATA filesystem, including kernel and initrd.
* Deploy using a script written by the test author (e.g. debootstrap)
  which is installed in the initial deployment. Parameters for the
  script need to be contained within the test image.

By keeping the downloaded image intact, it becomes possible to put the
LAVA extensions alongside the image instead of inside.

To make this work, several requirements must be met:

* The initial deployment must provide or support installation of all
  tools necessary to complete the second deployment - it is a TestError
  if there is insufficient space or the deployment cannot complete
  this step.
* The operation of the second deployment is a test shell which
  **precedes** the second boot. There is no provision for getting
  data back from this test shell into the boot arguments for the next
  boot. Any data which is genuinely persistent needs to be specified
  in advance.
* LAVA will need to support instructions in the job definition which
  determine whether a failed test shell should allow or skip the
  boot action following.
* LAVA will declare available media using the **kernel interface** as
  the label. A SATA drive which can only be attached to devices of a
  particular :term:`device type` using USB is still a USB device as it
  is constrained by the USB interface being present in the test image
  kernel. A SATA drive attached to a SATA connector on the board is a
  SATA device in LAVA (irrespective of how the board actually delivers
  the SATA interface on that connector).
* If a device has multiple media of the same type, it is up to the test
  writer to determine how to ensure that the correct image is booted.
  The ``blkid`` of a partition within an image is a permanent UUID within
  that image and needs to be determined in advance if this is to be used
  in arguments to the bootloader as the root filesystem.
* The manufacturer ID and serial number of the hardware to be used for
  the secondary deployment must be set in the device configuration. This
  makes it possible for test images to use such support as is available
  (e.g. ``udev``) to boot the correct device.
* The job definition needs to specify which hardware to use for the
  second deployment - if this label is based on a device node, it is a
  TestError if the use of this label does not result in a successful
  boot.
* The job definition also needs to specify the path to the kernel, dtb
  and the partition containing the rootfs within the deployed image.
* The job definition needs to include the bootloader commands, although
  defaults can be provided in some cases.

UUID vs device node support
---------------------------

A deployment to secondary media must be done by a running kernel, not
by the bootloader, so restrictions apply to that kernel:

#. Device types with more than one media device sharing the same device
   interface must be identifiable in the device_type configuration.
   These would be devices where, if all slots were populated, a full
   udev kernel would find explicitly more than one ``/dev/sd*`` top
   level device. It does not matter if these are physically different
   types of device (cubietruck has usb and sata) or the same type
   (d01 has three sata). The device_type declares the flag:
   ``UUID-required: True`` for each relevant interface. For cubietruck::

    media:  # two USB slots, one SATA connector
      usb:
        UUID-required: True
      sata:
        UUID-required: False

#. It is important to remember that there are five different identifiers
   involved across the device configuration and job submission:

   #. The ID of the device as it appears to the kernel running the deploy,
      provided by the device configuration: ``uuid``. This is found in
      ``/dev/disk/by-id/`` on a booted system.
   #. The ID of the device as it appears to the bootloader when reading
      deployed files into memory, provided by the device configuration:
      ``device_id``. This can be confirmed by interrupting the bootloader
      and listing the filesystem contents on the specified interface.
   #. The ID of the partition to specify as ``root`` on the kernel
      command line of the deployed kernel when booting the kernel inside
      the image, set by the job submission ``root_uuid``. Must be specified
      if the device has UUID-required set to True.
   #. The ``boot_part`` specified in the job submission which is the
      partition number inside the deployed image where the files can be
      found for the bootloader to execute. Files in this partition will
      be accessed directly through the bootloader, not via any mountpoint
      specified inside the image.
   #. The ``root_part`` specified in the job submission which is the
      partition number inside the deployed image where the root filesystem
      files can be found by the depoyed kernel, once booted. ``root_part``
      cannot be used with ``root_uuid`` - to do so causes a JobError.

Device configuration
^^^^^^^^^^^^^^^^^^^^

Media settings are per-device, based on the capability of the device type.
An individual devices of a specified type *may* have exactly one of the
available slots populated on any one interface. These individual devices
would set UUID-required: False for that interface. e.g. A panda has two
USB host slots. For each panda, if both slots are occupied, specify
``UUID-required: True`` in the device configuration. If only one is
occupied, specify ``UUID-required: False``. If none are occupied, comment
out or remove the entire ``usb`` interface section in the configuration
for that one device. List each specific device which is available as
media on that interface using a humand-usable string, e.g. a Sandisk
Ultra usb stick with a UUID of ``usb-SanDisk_Ultra_20060775320F43006019-0:0``
could simply be called ``SanDisk_Ultra``. Ensure that this label is
unique for each device on the same interface. Jobs will specify this label
in order to look up the actual UUID, allowing physical media to be
replaced with an equivalent device without changing the job submission data.

The device configuration should always include the UUID for all media on
each supported interface, even if ``UUID-required`` is False. The UUID is
the recommended way to specify the media, even when not strictly required.
Record the symlink name (without the path) for the top level device in
``/dev/disk/by-id/`` for the media concerned, i.e. the symlink pointing
at ``../sda`` not the symlink(s) pointing at individual partitions. The
UUID should be **quoted** to ensure that the YAML can be parsed correctly.
Also include the ``device_id`` which is the bootloader view of the same
device on this interface.

.. code-block:: yaml

 device_type: cubietruck
 commands:
  connect: telnet localhost 6000
 media:
   usb:  # bootloader interface name
     UUID-required: True  # cubie1 is pretending to have two usb media attached
     SanDisk_Ultra:
       uuid: "usb-SanDisk_Ultra_20060775320F43006019-0:0"  # /dev/disk/by-id/
       device_id: 0  # the bootloader device id for this media on the 'usb' interface

There is no reasonable way for the device configuration to specify the
device node as it may depend on how the deployed kernel or image is configured.
When this is used, the job submission must contain this data.

Deploy commands
"""""""""""""""

This is an example block - the actual data values here are known not to
work as the ``deploy`` step is for a panda but the ``boot`` step in the
next example comes from a working cubietruck job.

This example uses a device configuration where ``UUID-required`` is True.

For simplicity, this example also omits the initial deployment and boot,
at the start of this block, the device is already running a kernel with
a ramdisk or rootfs which provides enough support to complete this second
deployment.

.. code-block:: yaml

    # secondary media - use the first deploy to get to a system which can deploy the next
    # in testing, assumed to already be deployed
    - deploy:
        timeout:
          minutes: 10
        to: usb
        os: debian
        # not a real job, just used for unit tests
        compression: gz
        image: http://releases.linaro.org/12.02/ubuntu/leb-panda/panda-ubuntu-desktop.img.gz
        device: SanDisk_Ultra # needs to be exposed in the device-specific UI
        download: /usr/bin/wget


#. Ensure that the ``deploy`` action has sufficient time to download the
   **decompressed** image **and** write that image directly to the media
   using STDOUT. In the example, the deploy timeout has been set to ten
   minutes - in a test on the panda, the actual time required to write
   the specified image to a USB device was around 6 minutes.
#. Note the deployment strategy - ``to: usb``. This is a direct mapping
   to the kernel interface used to deploy and boot this image. The
   bootloader must also support reading files over this interface.
#. The compression method used by the specified image is explicitly set.
#. The image is downloaded and decompressed by the dispatcher, then made
   available to the device to retrieve and write to the specified media.
#. The device is specified as a label so that the correct UUID can be
   constructed from the device configuration data.
#. The download tool is specified as a full path which must exist inside
   the currently deployed system. This tool will be used to retrieve the
   decompressed image from the dispatcher and pass STDOUT to ``dd``. If
   the download tool is the default ``/usr/bin/wget``, LAVA will add the
   following options:
   ``--no-check-certificate --no-proxy --connect-timeout=30 -S --progress=dot:giga -O -``
   If different download tools are required for particular images, these
   can be specified, however, if those tools require options, the writer
   can either ensure that a script exists in the image which wraps those
   options or file a bug to have the alternative tool options supported.

The kernel inside the initial deployment **MUST** support UUID when
deployed on a device where UUID is required, as it is this kernel which
needs to make ``/dev/disk/by-id/$path`` exist for ``dd`` to use.

Boot commands
"""""""""""""

.. code-block:: yaml

    - boot:
        method: u-boot
        commands: usb
        parameters:
          shutdown-message: "reboot: Restarting system"
        # these files are part of the image already deployed and are known to the test writer
        kernel: /boot/vmlinuz-3.16.0-4-armmp-lpae
        ramdisk: /boot/initrd.img-3.16.0-4-armmp-lpae.u-boot
        dtb: /boot/dtb-3.16.0-4-armmp-lpae'
        root_uuid: UUID=159d17cc-697c-4125-95a0-a3775e1deabe  # comes from the supplied image.
        boot_part: 1  # the partition on the media from which the bootloader can read the kernel, ramdisk & dtb
        type: bootz

The ``kernel`` and (if specified) the ``ramdisk`` and ``dtb`` paths are
the paths used by the bootloader to load the files in order to boot the
image deployed onto the secondary media. These are **not necessarily**
the same as the paths to the same files as they would appear inside the
image after booting, depending on whether any boot partition is mounted
at a particular mountpoint.

The ``root_uuid`` is the full option for the ``root=`` command to the
kernel, including the ``UUID=`` prefix.

The ``boot_part`` is the number of the partition from which the bootloader
can read the files to boot the image. This will be combined with the
device configuration interface name and device_id to create the command
to the bootloader, e.g.::

 "setenv loadfdt 'load usb 0:1 ${fdt_addr_r} /boot/dtb-3.16.0-4-armmp-lpae''",

The dispatcher does NOT analyze the incoming image - internal UUIDs
inside an image do not change as the refactored dispatcher does **not**
break up or relay the partitions. Therefore, the UUIDs of partitions inside
the image **MUST** be declared by the job submissions.

Secondary connections
=====================

The implementation of VMGroups created a role for a delayed start
Multinode job. This would allow one job to operate over serial, publish
the IP address, start an SSH server and signal the second job that a
connection is ready to be established. This may be useful for situations
where a debugging shell needs to be opened around a virtualisation
boundary.

Device configuration design
===========================

Device configuration has moved to YAML and has a larger scope of possible
methods, related to the pipeline strategies.

Changes from existing configuration
-----------------------------------

The device configuration is moving off the dispatcher and into the main
LAVA server database. This simplifies the scheduler and is a step
towards a dumb dispatcher model where the dispatcher receives all device
configuration along with the job instead of deciding which jobs to run
based on local configuration. There is then no need for the device
configuration to include the hostname in the YAML as there is nothing
on the dispatcher to check against - the dispatcher uses the command
line arguments.

Example device configuration
----------------------------

.. code-block:: yaml

 device_type: beaglebone-black
 commands:
   connect: telnet localhost 6000
   hard_reset: /usr/bin/pduclient --daemon localhost --hostname pdu --command reboot --port 08
   power_off: /usr/bin/pduclient --daemon localhost --hostname pdu --command off --port 08
   power_on: /usr/bin/pduclient --daemon localhost --hostname pdu --command on --port 08

Example device_type configuration
---------------------------------

.. code-block:: yaml

 # replacement device_type config for the beaglebone-black type

 parameters:
  bootm:
   kernel: '0x80200000'
   ramdisk: '0x81600000'
   dtb: '0x815f0000'
  bootz:
   kernel: '0x81000000'
   ramdisk: '0x82000000'
   dtb: '0x81f00000'

 actions:
  deploy:
    # list of deployment methods which this device supports
    methods:
      # - image # not ready yet
      - tftp

  boot:
    # list of boot methods which this device supports.
    methods:
      - u-boot:
          parameters:
            bootloader_prompt: U-Boot
            boot_message: Booting Linux
            send_char: False
            # interrupt: # character needed to interrupt u-boot, single whitespace by default
          # method specific stanza
          oe:
            commands:
            - setenv initrd_high '0xffffffff'
            - setenv fdt_high '0xffffffff'
            - setenv bootcmd 'fatload mmc 0:3 0x80200000 uImage; fatload mmc 0:3 0x815f0000 board.dtb;
              bootm 0x80200000 - 0x815f0000'
            - setenv bootargs 'console=ttyO0,115200n8 root=/dev/mmcblk0p5 rootwait ro'
            - boot
          nfs:
            commands:
            - setenv autoload no
            - setenv initrd_high '0xffffffff'
            - setenv fdt_high '0xffffffff'
            - setenv kernel_addr_r '{KERNEL_ADDR}'
            - setenv initrd_addr_r '{RAMDISK_ADDR}'
            - setenv fdt_addr_r '{DTB_ADDR}'
            - setenv loadkernel 'tftp ${kernel_addr_r} {KERNEL}'
            - setenv loadinitrd 'tftp ${initrd_addr_r} {RAMDISK}; setenv initrd_size ${filesize}'
            - setenv loadfdt 'tftp ${fdt_addr_r} {DTB}'
            # this could be a pycharm bug or a YAML problem with colons. Use &#58; for now.
            # alternatively, construct the nfsroot argument from values.
            - setenv nfsargs 'setenv bootargs console=ttyO0,115200n8 root=/dev/nfs rw nfsroot={SERVER_IP}&#58;{NFSROOTFS},tcp,hard,intr ip=dhcp'
            - setenv bootcmd 'dhcp; setenv serverip {SERVER_IP}; run loadkernel; run loadinitrd; run loadfdt; run nfsargs; {BOOTX}'
            - boot
          ramdisk:
            commands:
            - setenv autoload no
            - setenv initrd_high '0xffffffff'
            - setenv fdt_high '0xffffffff'
            - setenv kernel_addr_r '{KERNEL_ADDR}'
            - setenv initrd_addr_r '{RAMDISK_ADDR}'
            - setenv fdt_addr_r '{DTB_ADDR}'
            - setenv loadkernel 'tftp ${kernel_addr_r} {KERNEL}'
            - setenv loadinitrd 'tftp ${initrd_addr_r} {RAMDISK}; setenv initrd_size ${filesize}'
            - setenv loadfdt 'tftp ${fdt_addr_r} {DTB}'
            - setenv bootargs 'console=ttyO0,115200n8 root=/dev/ram0 ip=dhcp'
            - setenv bootcmd 'dhcp; setenv serverip {SERVER_IP}; run loadkernel; run loadinitrd; run loadfdt; {BOOTX}'
            - boot
