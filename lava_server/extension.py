# Copyright (C) 2010, 2011 Linaro Limited
#
# Author: Zygmunt Krynicki <zygmunt.krynicki@linaro.org>
# Author: Michael Hudson-Doyle <michael.hudson@linaro.org>
#
# This file is part of LAVA Server.
#
# LAVA Server is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License version 3
# as published by the Free Software Foundation
#
# LAVA Server is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with LAVA Server.  If not, see <http://www.gnu.org/licenses/>.


from abc import ABCMeta, abstractmethod, abstractproperty
import logging
import pkg_resources


class ILavaServerExtension(object):
    """
    Interface for LAVA Server extensions.
    """

    __metaclass__ = ABCMeta

    @abstractmethod
    def contribute_to_settings(self, settings_module):
        """
        Add elements required to initialize this extension into the project
        settings module.
        """

    @abstractmethod
    def contribute_to_settings_ex(self, settings_module, settings_object):
        """
        This method is similar to contribute_to_settings() but allows
        implementation to access a settings object from django-debian. This
        allows extensions to read settings provided by local system
        administrator.
        """

    @abstractmethod
    def contribute_to_urlpatterns(self, urlpatterns):
        """
        Add application specific URLs to root URL patterns of lava-server
        """

    @abstractproperty
    def api_class(self):
        """
        Subclass of linaro_django_xmlrpc.models.ExposedAPI for this extension.

        The methods of the class returned from here will be available at /RPC2
        under the name used to register the extension.  Return None if no
        methods should be added.
        """

    @abstractproperty
    def name(self):
        """
        Name of this extension.
        """

    @abstractproperty
    def version(self):
        """
        Version of this extension.
        """

    @abstractmethod
    def get_main_url(self):
        """
        Absolute URL of the main view
        """


class LavaServerExtension(ILavaServerExtension):
    """
    LAVA Server extension class.

    Implements basic behavior for LAVA server extensions
    """

    def __init__(self, slug):
        self.slug = slug

    @abstractproperty
    def app_name(self):
        """
        Name of this extension's primary django application.
        """

    @abstractproperty
    def main_view_name(self):
        """
        Name of the main view
        """

    @property
    def api_class(self):
        """
        Subclass of linaro_django_xmlrpc.models.ExposedAPI for this extension.

        Return None by default for no API.
        """
        return None

    def contribute_to_settings(self, settings_module):
        settings_module['INSTALLED_APPS'].append(self.app_name)
        settings_module['PREPEND_LABEL_APPS'].append(self.app_name)

    def contribute_to_settings_ex(self, settings_module, settings_object):
        pass

    def contribute_to_urlpatterns(self, urlpatterns):
        from django.conf.urls.defaults import url, include 
        urlpatterns += [
            url(r'^{slug}/'.format(slug=self.slug),
                include('{app_name}.urls'.format(app_name=self.app_name)))]

    def get_main_url(self):
        from django.core.urlresolvers import reverse
        return reverse(self.main_view_name)



class ExtensionLoadError(Exception):
    """
    Exception internally raised by extension loader
    """

    def __init__(self, extension, message):
        self.extension = extension
        self.message = message

    def __repr__(self):
        return "ExtensionLoadError(extension={0!r}, message={1!r})".format(
            self.extension, self.message)


class ExtensionLoader(object):
    """
    Helper to load extensions
    """

    def __init__(self):
        self._extensions = None  # Load this lazily so that others can import this module
        self._mapper = None

    @property
    def xmlrpc_mapper(self):
        if self._mapper is None:
            from lava_server.xmlrpc import LavaMapper
            mapper = LavaMapper()
            mapper.register_introspection_methods()
            for extension in self.extensions:
                api_class = extension.api_class
                if api_class is not None:
                    mapper.register(api_class, extension.slug)
            self._mapper = mapper
        return self._mapper

    @property
    def extensions(self):
        """
        List of extensions
        """
        if self._extensions is None:
            self._extensions = []
            for name in self._find_extensions():
                try:
                    extension = self._load_extension(name)
                except ExtensionLoadError as ex:
                    logging.exception("Unable to load extension %r: %s", name, ex.message)
                else:
                    self._extensions.append(extension)
        return self._extensions

    def contribute_to_settings(self, settings_module, settings_object=None):
        """
        Contribute to lava-server settings module.

        The settings_object is optional (it may be None) and allows extensions
        to look at the django-debian settings object. The settings_module
        argument is a magic dictionary returned by locals()
        """
        for extension in self.extensions:
            extension.contribute_to_settings(settings_module)
            if settings_object is not None:
                extension.contribute_to_settings_ex(settings_module, settings_object)

    def contribute_to_urlpatterns(self, urlpatterns):
        """
        Contribute to lava-server URL patterns
        """
        for extension in self.extensions:
            extension.contribute_to_urlpatterns(urlpatterns)

    def _find_extensions(self):
        return sorted(
            pkg_resources.iter_entry_points(
                'lava_server.extensions'),
            key=lambda ep:ep.name)

    def _load_extension(self, entrypoint):
        """
        Load extension specified by the given name.
        Name must be a string like "module:class". Module may be a
        package with dotted syntax to address specific module.

        @return Imported extension instance, subclass of ILavaServerExtension
        @raises ExtensionLoadError
        """
        try:
            extension_cls = entrypoint.load()
        except ImportError as ex:
            logging.exception("Unable to load extension entry point: %r", entrypoint)
            raise ExtensionLoadError(
                entrypoint,
                "Unable to load extension entry point")
        if not issubclass(extension_cls, ILavaServerExtension):
            raise ExtensionLoadError(
                extension_cls,
                "Class does not implement ILavaServerExtension interface")
        try:
            extension = extension_cls(entrypoint.name)
        except:
            raise ExtensionLoadError(
                extension_cls, "Unable to instantiate class")
        return extension


# Global loader instance
loader = ExtensionLoader()