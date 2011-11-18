Installation
^^^^^^^^^^^^

LAVA can be installed in several different ways. As with any open source
project that does source distribution the end user has all the freedom to do
what they want. We support certain installation methods more than others. You
can always ask for support using Launchpad support tracker (see
:ref:`questions`)

Using virtualenv
******************

Python Virtualenv is a useful tool for creating a sandbox for working
with python modules.  In Ubuntu, you can get it by installing
*python-virtualenv* using apt-get.  For source and pypi installations of
non-production systems, it is highly recommended.

Example usage ::

 $ virtualenv sandbox
 $ cd sandbox
 $ . bin/activate

Once activated, the environment for that session will be set up so that
subsequent commands will use the virtual environment settings.

Installation from source
************************

This is the most complicated and error prone installation method. It requires
the user to download source release tarballs. Unpack them and install them in
the correct order. Depending on the exact set of components that are installed
(especially client or server side components) some additional steps are
necessary. This may include setting up the web application host (one of many
possible configurations here), setting up the database (again multiple possible
options, our recommendation is to use the latest stable version of PostgreSQL).

For installing from source, it's normally much simpler to install from
pypi first, then update using the source.  This is useful if you want
to use it for development against your own branch.  For instance, after
installing from pypi (see directions below) you could do the following.

Updating in a virtualenv using source ::

 $ bzr branch lp:lava-server
 $ cd lava-server
 $ ./setup develop

Installation from PypI
**********************

PyPi is the python package index (http://pypi.python.org/pypi). It is
maintained by the python community and is the preferred distribution method
used by many open source projects written in the python programming language.

Here a front-end program, such as pip (http://pypi.python.org/pypi/pip) is used
to install packages, and their python dependencies. Pip finds the required set
of packages, downloads their source releases and does the hard work of figuring
out the right way to put them together.

This is the best compromise between wide system support (any flavour of Linux,
OS X and Windows), simplicity, upgrade and availability. The downside is that
it does not handle, by itself, the last mile. This method does not handle
things like setting up and running the application server. It also requires the
user to have additional development packages, such as python header files,
database server header files, the C compiler and more.

To install using pypi (For development only, not for production)::
 $ pip install lava-server
 $ lava-server manage --development syncdb
 $ lava-server manage --development migrate

You will need to answer a few questions during the syncdb step.  This
will use a simple sqlite database, and should normally only be used for
testing or hacking on lava-server.

.. todo::
 Installation instructions for production installations against
 postgresql using pypi

Installation from PPA
*********************

This method is only suitable for users running Ubuntu 10.04 or later. Here LAVA
is pre-compiled and packaged as Debian packages (debs). The installation
scripts embedded in the packages take care for setting up additional services
so usually this is the best method to quickly have a self-contained running
installation. The downside is longer release period as packaging takes
additional time after each release. Another downside is that our support is
limited to Ubuntu.

To install using the ppa ::

 $ sudo add-apt-repository ppa:linaro-validation/ppa
 $ sudo apt-get update
 $ sudo apt-get install lava-server
