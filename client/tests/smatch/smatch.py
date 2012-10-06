import os, logging, commands
from autotest.client import kernel, test, utils
from autotest.client.shared import software_manager


class smatch(test.test):
    version = 1

    def initialize(self):
        self.job.require_gcc()

    def setup(self, tarball='smatch-b0e645.tar.bz2'):
        self.tarball = utils.unmap_url(self.bindir, tarball, self.tmpdir)
        utils.extract_tarball_to_dir(self.tarball, self.srcdir)

        sm = software_manager.SoftwareManager()
        for header in ['/usr/include/sqlite3.h', '/usr/include/llvm']:
            if not os.access(header, os.X_OK):
                logging.debug("%s missing - trying to install", header)
                pkg = sm.provides(header)
                if pkg is None:
                    raise InstallError (
                        "Unable to find header %s to satisfy 'smatch' dependence" %
                        header)
                else:
                    sm.install(pkg)

        os.chdir(self.srcdir)
        utils.make('-j %s' %  2 * utils.count_cpus())

    def execute(self, kernel = None, base_tree = None):

        make_opts = 'C=1 CHECK="%s -p=kernel"' % os.path.join(self.srcdir, 'smatch')
        if kernel == None and base_tree == None:
            raise TestError("Test require at least one parameter")

        if not kernel:
            kernel = self.job.kernel(base_tree)
            kernel.config()

        logfile = os.path.join(self.resultsdir, 'smatch_log')

        kernel.build(make_opts, logfile)
        
        # It is reasonable to put errors and warnings to separate file
        errlog = os.path.join(self.resultsdir, 'smatch_error')
        utils.system("egrep '(warn|error):' %s | tee %s" % (logfile, errlog))

        # Collect statistics keyval
        cmd = "egrep 'error:' %s | wc -l" % logfile
        (ret, nr_err) = commands.getstatusoutput(cmd)
        if ret != 0:
            raise CmdError("Command '%s' failed with ret = %s" % (cmd, ret))

        self.write_test_keyval({'error_count': nr_err})
        cmd = "egrep 'warn:' %s | wc -l" % logfile
        (ret, nr_warn) = commands.getstatusoutput(cmd)
        if ret != 0:
            raise CmdError("Command '%s' failed with ret = %s" % (cmd, ret))
        self.write_test_keyval({'warning_count': nr_warn})
