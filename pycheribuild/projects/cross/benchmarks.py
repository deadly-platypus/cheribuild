#
# Copyright (c) 2018 Alex Richardson
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory under DARPA/AFRL contract FA8750-10-C-0237
# ("CTSRD"), as part of the DARPA CRASH research programme.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.
#

from .crosscompileproject import *
from ..project import ReuseOtherProjectRepository
from ...config.loader import ConfigOptionBase
from ...utils import setEnv, IS_FREEBSD, commandline_to_str, is_jenkins_build
from pathlib import Path
import inspect
import datetime
import tempfile


class BuildMibench(CrossCompileProject):
    repository = GitRepository("git@github.com:CTSRD-CHERI/mibench")
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    projectName = "mibench"
    # Needs bsd make to build
    make_kind = MakeCommandKind.BsdMake
    # and we have to build in the source directory
    build_in_source_dir = True
    # Keep the old bundles when cleaning
    _extra_git_clean_excludes = ["--exclude=*-bundle"]

    @property
    def bundle_dir(self):
        return Path(self.buildDir, self.get_crosscompile_target(self.config).value +
                    self.build_configuration_suffix() + "-bundle")

    @property
    def benchmark_version(self):
        if self.compiling_for_host():
            return "x86"
        if self.compiling_for_mips():
            return "mips-asan" if self.compiling_for_mips() else "mips"
        if self.compiling_for_cheri():
            return "cheri" + self.config.cheriBitsStr
        raise ValueError("Unsupported target architecture!")

    def compile(self, **kwargs):
        with setEnv(MIPS_SDK=self.config.sdkDir,
                    CHERI128_SDK=self.config.sdkDir,
                    CHERI256_SDK=self.config.sdkDir,
                    CHERI_SDK=self.config.sdkDir):
            # We can't fall back to /usr/bin/ar here since that breaks on MacOS
            self.make_args.set(AR=str(self.config.sdkBinDir / "ar") + " rc")
            self.make_args.set(AR2=str(self.config.sdkBinDir / "ranlib"))
            self.make_args.set(RANLIB=str(self.config.sdkBinDir / "ranlib"))
            self.make_args.set(ADDITIONAL_CFLAGS=commandline_to_str(self.default_compiler_flags))
            self.make_args.set(ADDITIONAL_LDFLAGS=commandline_to_str(self.default_ldflags))
            self.make_args.set(VERSION=self.benchmark_version)
            if self.compiling_for_mips():
                self.make_args.set(MIPS_SYSROOT=self.config.get_sysroot_path(CrossCompileTarget.MIPS))
            if self.compiling_for_cheri():
                if self.config.cheriBits == 128:
                    self.make_args.set(VERSION="cheri128", CHERI128_SYSROOT=self.config.cheriSysrootDir)
                else:
                    assert self.config.cheriBits == 256
                    self.make_args.set(VERSION="cheri256", CHERI256_SYSROOT=self.config.cheriSysrootDir)
            self.makedirs(self.buildDir / "bundle")
            self.make_args.set(BUNDLE_DIR=self.buildDir / self.bundle_dir)
            self.runMake("bundle_dump", cwd=self.sourceDir)
            if self.compiling_for_mips() and self.use_asan:
                self.copy_asan_dependencies(self.buildDir / "bundle/lib")

    def install(self, **kwargs):
        if is_jenkins_build():
            self.makedirs(self.installDir)
            self.run_cmd("cp", "-av", self.bundle_dir, self.installDir, cwd=self.buildDir)
            self.run_cmd("du", "-sh", self.installDir)
            # Remove all the .dump files from the tarball
            self.run_cmd("find", self.installDir, "-name", "*.dump", "-delete")
            self.run_cmd("du", "-sh", self.installDir)
        else:
            self.info("Not installing MiBench for non-Jenkins builds")

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("running x86 tests is not implemented yet")
        # testing, not benchmarking -> run only once: (-s small / -s large?)
        test_command = "cd '/build/" + self.bundle_dir.name + "' && ./run_jenkins-bluehive.sh -d0 -r1 -s small " + self.benchmark_version
        self.run_cheribsd_test_script("run_simple_tests.py", "--test-command", test_command,
                                      "--test-timeout", str(120 * 60),
                                      mount_builddir=True)


class BuildOlden(CrossCompileProject):
    repository = GitRepository("git@github.com:CTSRD-CHERI/olden")
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    projectName = "olden"
    # Needs bsd make to build
    make_kind = MakeCommandKind.BsdMake
    # and we have to build in the source directory
    build_in_source_dir = True

    def compile(self, **kwargs):
        with setEnv(MIPS_SDK=self.config.sdkDir,
                    CHERI128_SDK=self.config.sdkDir,
                    CHERI256_SDK=self.config.sdkDir,
                    CHERI_SDK=self.config.sdkDir):
            self.make_args.set(SYSROOT_DIRNAME=self.crossSysrootPath.name)
            self.make_args.add_flags("-f", "Makefile.jenkins")
            self.make_args.set(ADDITIONAL_CFLAGS=commandline_to_str(self.default_compiler_flags))
            self.make_args.set(ADDITIONAL_LDFLAGS=commandline_to_str(self.default_ldflags))
            if self.compiling_for_host():
                self.runMake("x86")
            if self.compiling_for_mips():
                self.runMake("mips-asan" if self.use_asan else "mips")
            if self.compiling_for_cheri():
                if self.config.cheriBits == 128:
                    self.runMake("cheriabi128")
                else:
                    assert self.config.cheriBits == 256
                    self.runMake("cheriabi256")
        # copy asan libraries and the run script to the bin dir to ensure that we can run with --test from the
        # build directory.
        self.installFile(self.sourceDir / "run_jenkins-bluehive.sh",
                         self.buildDir / "bin/run_jenkins-bluehive.sh", force=True)
        if self.compiling_for_mips() and self.use_asan:
            self.copy_asan_dependencies(self.buildDir / "bin/lib")

    @property
    def test_arch_suffix(self):
        if self.compiling_for_host():
            return "x86"
        if self.compiling_for_cheri():
            return "cheri" + self.config.cheriBitsStr
        else:
            assert self.compiling_for_mips(), "other arches not support"
            return "mips-asan" if self.use_asan else "mips"

    def install(self, **kwargs):
        self.makedirs(self.installDir)
        for script in ("run_micro2016.sh", "run_isca2017.sh", "run_jenkins-bluehive.sh"):
            self.installFile(self.sourceDir / script, self.installDir / script, force=True)
        if Path(self.sourceDir / "bin").exists():
            for file in Path(self.sourceDir / "bin").iterdir():
                if file.is_file() and file.name.endswith(".bench"):
                    self.installFile(file, self.installDir / file.name, force=True)
        if is_jenkins_build():
            if self.compiling_for_mips() and self.use_asan:
                self.copy_asan_dependencies(self.installDir / "lib")
            # Remove all the .dump files from the tarball
            self.run_cmd("find", self.installDir, "-name", "*.dump", "-delete")
            self.run_cmd("du", "-sh", self.installDir)

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("running x86 tests is not implemented yet")
        # testing, not benchmarking -> run only once: (-s small / -s large?)
        test_command = "cd /build/bin && ./run_jenkins-bluehive.sh -d0 -r1 {tgt}".format(tgt=self.test_arch_suffix)
        self.run_cheribsd_test_script("run_simple_tests.py", "--test-command", test_command,
                                      "--test-timeout", str(120 * 60),
                                      mount_builddir=True)

    def run_benchmarks(self):
        statcounters_name = "olden-statcounters{}-{}.csv".format(
            self.build_configuration_suffix(), datetime.datetime.now().strftime("%Y%m%d_%H-%M-%S"))
        self._fpga_benchmark(self.buildDir / "bin", output_file=statcounters_name,
                             benchmark_script_args=["-d1", "-r5", "-o", statcounters_name, self.test_arch_suffix])

    def _fpga_benchmark(self, benchmarks_dir: Path, *, output_file: str=None, benchmark_script: str=None,
                        benchmark_script_args: list=None, extra_runbench_args: list=None):
        assert benchmarks_dir is not None
        assert output_file is not None, "output_file must be set to a valid value"
        extra_args = [benchmarks_dir, "--target=" + self.config.benchmark_ssh_host, "--out-path=" + output_file]
        if self.config.benchmark_extra_args:
            extra_args.extend(self.config.benchmark_extra_args)
        if self.config.tests_interact:
            extra_args.append("--interact")
        if not self.config.benchmark_clean_boot:
            extra_args.append("--skip-boot")
        if benchmark_script:
            extra_args.append("--script-name=" + benchmark_script)
        if benchmark_script_args:
            extra_args.append("--script-args=" + commandline_to_str(benchmark_script_args))
        if extra_runbench_args:
            extra_args.extend(extra_runbench_args)
        beri_fpga_bsd_boot_script = """
source "{cheri_svn}/setup.sh"
export PATH="$PATH:{cherilibs_svn}/tools:{cherilibs_svn}/tools/debug"
exec beri-fpga-bsd-boot.py -vvvvv runbench {runbench_args}
""".format(cheri_svn=self.config.cheri_svn_checkout, cherilibs_svn=self.config.cherilibs_svn_checkout,
           runbench_args=commandline_to_str(extra_args))
        self.runShellScript(beri_fpga_bsd_boot_script, shell="bash") # the setup script needs bash not sh

class BuildSpec2006(CrossCompileProject):
    target = "spec2006"
    projectName = "spec2006"
    # No repository to clone (just hack around this):
    repository = ReuseOtherProjectRepository(BuildOlden, ".")
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    make_kind = MakeCommandKind.GnuMake

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.spec_iso = cls.addPathOption("spec-iso", help="Path to the spec ISO image")
        cls.spec_config_dir = cls.addPathOption("spec-config-dir", help="Path to the CHERI spec config files")
        cls.spec_base_dir = cls.addPathOption("spec-base-dir", help="Path to the CHERI spec build scripts")

    @property
    def config_name(self):
        if self.compiling_for_mips():
            build_arch = "mips-" + self.linkage().value
            float_abi = self.config.mips_float_abi.name.lower() + "fp"
            return "freebsd-" + build_arch + "-" + float_abi
        elif self.compiling_for_cheri():
            build_arch = "cheri" + self.config.cheri_bits_and_abi_str + "-" + self.linkage().value
            float_abi = self.config.mips_float_abi.name.lower() + "fp"
            return "freebsd-" + build_arch + "-" + float_abi
        else:
            self.fatal("NOT SUPPORTED YET")
            return "EROROR"

    @property
    def hw_cpu(self):
        if self.compiling_for_mips():
            return "BERI"
        elif self.compiling_for_cheri():
            return "CHERI" + self.config.cheri_bits_and_abi_str
        return "unknown"

    def compile(self, cwd: Path = None):
        for attr in ("spec_iso", "spec_config_dir", "spec_base_dir"):
            if not getattr(self, attr):
                option = inspect.getattr_static(self, attr)
                assert isinstance(option, ConfigOptionBase)
                self.fatal("Required SPEC path is not set! Please set", option.fullOptionName)
                return
        self.makedirs(self.buildDir / "spec")
        if not (self.buildDir / "spec/README-CTSRD.txt").exists():
            self.cleanDirectory(self.buildDir / "spec")  # clean up partial builds
            self.run_cmd("bsdtar", "xf", self.spec_iso, "-C", "spec", cwd=self.buildDir)
            self.run_cmd("chmod", "-R", "u+w", "spec/", cwd=self.buildDir)
            for dir in Path(self.spec_base_dir).iterdir():
                self.run_cmd("cp", "-a", dir, ".", cwd=self.buildDir / "spec")
            self.run_cmd(self.buildDir / "spec/install.sh", "-f", cwd=self.buildDir / "spec")


        config_file_text = Path(self.spec_config_dir / "freebsd-cheribuild.cfg").read_text()

        config_file_text = config_file_text.replace("@HW_CPU@", self.hw_cpu)
        config_file_text = config_file_text.replace("@CONFIG_NAME@", self.config_name)

        config_file_text = config_file_text.replace("@CLANG@", str(self.CC))
        config_file_text = config_file_text.replace("@CLANGXX@", str(self.CXX))
        config_file_text = config_file_text.replace("@CFLAGS@", commandline_to_str(self.default_compiler_flags + self.CFLAGS))
        config_file_text = config_file_text.replace("@CXXFLAGS@", commandline_to_str(self.default_compiler_flags + self.CXXFLAGS))
        config_file_text = config_file_text.replace("@LDFLAGS@", commandline_to_str(self.default_ldflags + self.LDFLAGS))
        config_file_text = config_file_text.replace("@SYSROOT@", str(self.sdkSysroot))
        config_file_text = config_file_text.replace("@SYS_BIN@", str(self.config.sdkBinDir))

        self.writeFile(self.buildDir / "spec/config/" / (self.config_name + ".cfg"), contents=config_file_text,
                       overwrite=True, noCommandPrint=False, mode=0o644)
        # Worst case benchmarks: 471.omnetpp 483.xalancbmk 400.perlbench
        benchmark_list = "471"
        script = """
source shrc
runspec -c {spec_config_name} --noreportable --make_bundle {spec_config_name} {benchmark_list}
""".format(benchmark_list=benchmark_list, spec_config_name=self.config_name)
        self.writeFile(self.buildDir / "build.sh", contents=script, mode=0o755, overwrite=True)
        self.run_cmd("sh", "-x", self.buildDir / "build.sh", cwd=self.buildDir / "spec")

    def install(self, **kwargs):
        pass

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("running host tests is not implemented yet")
        # self.makedirs(self.buildDir / "test")
        #self.run_cmd("tar", "-xvjf", self.buildDir / "spec/{}.cpu2006bundle.bz2".format(self.config_name),
        #             cwd=self.buildDir / "test")
        #self.run_cmd("find", ".", cwd=self.buildDir / "test")
        test_command = """
export LD_LIBRARY_PATH=/sysroot/usr/lib:/sysroot/lib;
export LD_CHERI_LIBRARY_PATH=/sysroot/usr/libcheri;
cd /build/spec/benchspec/CPU2006/471.omnetpp/ && ./exe/omnetpp_base.{config} -f data/test/input/omnetpp.ini""".format(config=self.config_name)
        self.run_cheribsd_test_script("run_simple_tests.py", "--test-command", test_command,
                                      "--test-timeout", str(120 * 60),
                                      mount_builddir=True, mount_sysroot=True)
