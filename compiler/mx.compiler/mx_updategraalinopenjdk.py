#
# ----------------------------------------------------------------------------------------------------
#
# Copyright (c) 2018, 2018, Oracle and/or its affiliates. All rights reserved.
# DO NOT ALTER OR REMOVE COPYRIGHT NOTICES OR THIS FILE HEADER.
#
# This code is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 2 only, as
# published by the Free Software Foundation.  Oracle designates this
# particular file as subject to the "Classpath" exception as provided
# by Oracle in the LICENSE file that accompanied this code.
#
# This code is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
# version 2 for more details (a copy is included in the LICENSE file that
# accompanied this code).
#
# You should have received a copy of the GNU General Public License version
# 2 along with this work; if not, write to the Free Software Foundation,
# Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Please contact Oracle, 500 Oracle Parkway, Redwood Shores, CA 94065 USA
# or visit www.oracle.com if you need additional information or have any
# questions.
#
# ----------------------------------------------------------------------------------------------------

import os
import shutil
from collections import namedtuple
from argparse import ArgumentParser
from os.path import join, exists, dirname

import mx
import mx_compiler

def _read_sibling_file(basename):
    path = join(dirname(__file__), basename)
    with open(path, 'r') as fp:
        return fp.read()

def _find_version_base_project(versioned_project):
    extended_packages = versioned_project.extended_java_packages()
    if not extended_packages:
        mx.abort('Project with a multiReleaseJarVersion attribute must have sources in a package defined by project without multiReleaseJarVersion attribute', context=versioned_project)
    base_project = None
    base_package = None
    for extended_package in extended_packages:
        for p in mx.projects():
            if versioned_project != p and p.isJavaProject() and not hasattr(p, 'multiReleaseJarVersion'):
                if extended_package in p.defined_java_packages():
                    if base_project is None:
                        base_project = p
                        base_package = extended_package
                    else:
                        if base_project != p:
                            mx.abort('Multi-release jar versioned project {} must extend packages from exactly one project but extends {} from {} and {} from {}'.format(versioned_project, extended_package, p, base_project, base_package))
    if not base_project:
        mx.abort('Multi-release jar versioned project {} must extend package(s) from another project'.format(versioned_project))
    return base_project


SuiteJDKInfo = namedtuple('SuiteJDKInfo', 'name includes excludes')
GraalJDKModule = namedtuple('GraalJDKModule', 'name suites')

def updategraalinopenjdk(args):
    """updates the Graal sources in OpenJDK"""
    parser = ArgumentParser(prog='mx updategraalinopenjdk')
    parser.add_argument('--pretty', help='value for --pretty when logging the changes since the last JDK* tag')
    parser.add_argument('jdkrepo', help='path to the local OpenJDK repo')
    parser.add_argument('version', type=int, help='Java version of the OpenJDK repo')

    args = parser.parse_args(args)

    if mx_compiler.jdk.javaCompliance.value < args.version:
        mx.abort('JAVA_HOME/--java-home must be Java version {} or greater: {}'.format(args.version, mx_compiler.jdk))

    graal_modules = [
        # JDK module jdk.internal.vm.compiler is composed of sources from:
        GraalJDKModule('jdk.internal.vm.compiler',
            # 1. Classes in the compiler suite under the org.graalvm namespace except for packages
            #    whose names include "truffle" or "management"
            [SuiteJDKInfo('compiler', ['org.graalvm'], ['truffle', 'management']),
            # 2. Classes in the sdk suite under the org.graalvm.collections and org.graalvm.word namespaces
             SuiteJDKInfo('sdk', ['org.graalvm.collections', 'org.graalvm.word'], [])]),
        # JDK module jdk.internal.vm.compiler.management is composed of sources from:
        GraalJDKModule('jdk.internal.vm.compiler.management',
            # 1. Classes in the compiler suite under the org.graalvm.compiler.hotspot.management namespace
            [SuiteJDKInfo('compiler', ['org.graalvm.compiler.hotspot.management'], [])]),
        # JDK module jdk.aot is composed of sources from:
        GraalJDKModule('jdk.aot',
            # 1. Classes in the compiler suite under the jdk.tools.jaotc namespace
            [SuiteJDKInfo('compiler', ['jdk.tools.jaotc'], [])]),
    ]

    # Packages in Graal that have different names in OpenJDK so that the original packages can be deployed
    # as it on the class path and not clash with packages in the jdk.internal.vm.compiler module.
    package_renamings = {
        'org.graalvm.collections' : 'jdk.internal.vm.compiler.collections',
        'org.graalvm.word'        : 'jdk.internal.vm.compiler.word'
    }

    # Strings to be replaced in files copied to OpenJDK.
    replacements = {
        'published by the Free Software Foundation.  Oracle designates this\n * particular file as subject to the "Classpath" exception as provided\n * by Oracle in the LICENSE file that accompanied this code.' : 'published by the Free Software Foundation.',
        _read_sibling_file('upl_substring.txt') : _read_sibling_file('gplv2_substring.txt')
    }

    # Strings that must not exist in OpenJDK source files. This is applied after replacements are made.
    blacklist = ['"Classpath" exception']

    jdkrepo = args.jdkrepo

    for m in graal_modules:
        m_src_dir = join(jdkrepo, 'src', m.name)
        if not exists(m_src_dir):
            mx.abort(jdkrepo + ' does not look like a JDK repo - ' + m_src_dir + ' does not exist')

    def run_output(args, cwd=None):
        out = mx.OutputCapture()
        mx.run(args, cwd=cwd, out=out, err=out)
        return out.data

    for m in graal_modules:
        m_src_dir = join('src', m.name)
        mx.log('Checking ' + m_src_dir)
        out = run_output(['hg', 'status', m_src_dir], cwd=jdkrepo)
        if out:
            mx.abort(jdkrepo + ' is not "hg clean":' + '\n' + out[:min(200, len(out))] + '...')

    for dirpath, _, filenames in os.walk(join(jdkrepo, 'make')):
        for filename in filenames:
            if filename.endswith('.gmk'):
                filepath = join(dirpath, filename)
                with open(filepath) as fp:
                    contents = fp.read()
                new_contents = contents
                for old_name, new_name in package_renamings.iteritems():
                    new_contents = new_contents.replace(old_name, new_name)
                if new_contents != contents:
                    with open(filepath, 'w') as fp:
                        fp.write(new_contents)
                        mx.log('  updated ' + filepath)

    copied_source_dirs = []
    jdk_internal_vm_compiler_EXCLUDES = set() # pylint: disable=invalid-name
    for m in graal_modules:
        classes_dir = join(jdkrepo, 'src', m.name, 'share', 'classes')
        for info in m.suites:
            mx.log('Processing ' + m.name + ':' + info.name)
            for e in os.listdir(classes_dir):
                if any(inc in e for inc in info.includes) and not any(ex in e for ex in info.excludes):
                    project_dir = join(classes_dir, e)
                    shutil.rmtree(project_dir)
                    mx.log('  removed ' + project_dir)
            suite = mx.suite(info.name)

            worklist = []

            for p in [e for e in suite.projects if e.isJavaProject()]:
                if any(inc in p.name for inc in info.includes) and not any(ex in p.name for ex in info.excludes):
                    assert len(p.source_dirs()) == 1, p
                    version = 0
                    new_project_name = p.name
                    if hasattr(p, 'multiReleaseJarVersion'):
                        version = int(getattr(p, 'multiReleaseJarVersion'))
                        if version <= args.version:
                            base_project = _find_version_base_project(p)
                            new_project_name = base_project.name
                        else:
                            continue

                    for old_name, new_name in package_renamings.iteritems():
                        if new_project_name.startswith(old_name):
                            new_project_name = new_project_name.replace(old_name, new_name)

                    source_dir = p.source_dirs()[0]
                    target_dir = join(classes_dir, new_project_name, 'src')
                    copied_source_dirs.append(source_dir)

                    workitem = (version, p, source_dir, target_dir)
                    worklist.append(workitem)

            # Ensure versioned resources are copied in the right order
            # such that higher versions override lower versions.
            worklist = sorted(worklist)

            for version, p, source_dir, target_dir in worklist:
                first_file = True
                for dirpath, _, filenames in os.walk(source_dir):
                    for filename in filenames:
                        src_file = join(dirpath, filename)
                        dst_file = join(target_dir, os.path.relpath(src_file, source_dir))
                        with open(src_file) as fp:
                            contents = fp.read()
                        old_line_count = len(contents.split('\n'))
                        if filename.endswith('.java'):
                            for old_name, new_name in package_renamings.iteritems():
                                old_name_as_dir = old_name.replace('.', os.sep)
                                if old_name_as_dir in src_file:
                                    new_name_as_dir = new_name.replace('.', os.sep)
                                    dst = src_file.replace(old_name_as_dir, new_name_as_dir)
                                    dst_file = join(target_dir, os.path.relpath(dst, source_dir))
                                contents = contents.replace(old_name, new_name)
                            for old_line, new_line in replacements.iteritems():
                                contents = contents.replace(old_line, new_line)
                            new_line_count = len(contents.split('\n'))
                            if new_line_count > old_line_count:
                                mx.abort('Pattern replacement caused line count to grow from {} to {} in {}'.format(old_line_count, new_line_count, src_file))
                            else:
                                if new_line_count < old_line_count:
                                    contents = contents.replace('\npackage ', '\n' * (old_line_count - new_line_count) + '\npackage ')
                            new_line_count = len(contents.split('\n'))
                            if new_line_count != old_line_count:
                                mx.abort('Unable to correct line count for {}'.format(src_file))
                            for forbidden in blacklist:
                                if forbidden in contents:
                                    mx.abort('Found blacklisted pattern \'{}\' in {}'.format(forbidden, src_file))
                        dst_dir = os.path.dirname(dst_file)
                        if not exists(dst_dir):
                            os.makedirs(dst_dir)
                        if first_file:
                            mx.log('  copying: ' + source_dir)
                            mx.log('       to: ' + target_dir)
                            if p.testProject or p.definedAnnotationProcessors:
                                to_exclude = p.name
                                for old_name, new_name in package_renamings.iteritems():
                                    if to_exclude.startswith(old_name):
                                        sfx = '' if to_exclude == old_name else to_exclude[len(old_name):]
                                        to_exclude = new_name + sfx
                                        break
                                jdk_internal_vm_compiler_EXCLUDES.add(to_exclude)
                            first_file = False
                        with open(dst_file, 'w') as fp:
                            fp.write(contents)

    # Update jdk.internal.vm.compiler.EXCLUDES in make/CompileJavaModules.gmk
    # to exclude all test, benchmark and annotation processor packages.
    CompileJavaModules_gmk = join(jdkrepo, 'make', 'CompileJavaModules.gmk') # pylint: disable=invalid-name
    new_lines = []
    with open(CompileJavaModules_gmk) as fp:
        line_in_def = False
        for line in fp.readlines():
            stripped_line = line.strip()
            if line_in_def:
                if stripped_line == '#':
                    line_in_def = False
                    new_lines.append(line)
                else:
                    parts = stripped_line.split()
                    assert len(parts) == 2 and parts[1] == '\\', line
            elif stripped_line == 'jdk.internal.vm.compiler_EXCLUDES += \\':
                line_in_def = True
                new_lines.append(line)

                # Add org.graalvm.compiler.processor since it is only a dependency
                # for (most) Graal annotation processors and is not needed to
                # run Graal.
                jdk_internal_vm_compiler_EXCLUDES.add('org.graalvm.compiler.processor')

                for pkg in sorted(jdk_internal_vm_compiler_EXCLUDES):
                    new_lines.append('    ' + pkg + ' \\\n')
            else:
                new_lines.append(line)
    with open(CompileJavaModules_gmk, 'w') as fp:
        for line in new_lines:
            fp.write(line)

    mx.log('Adding new files to HG...')
    overwritten = ''
    for m in graal_modules:
        m_src_dir = join('src', m.name)
        out = run_output(['hg', 'log', '-r', 'last(keyword("Update Graal"))', '--template', '{rev}', m_src_dir], cwd=jdkrepo)
        last_graal_update = out.strip()
        if last_graal_update:
            overwritten += run_output(['hg', 'diff', '-r', last_graal_update, '-r', 'tip', m_src_dir], cwd=jdkrepo)
        mx.run(['hg', 'add', m_src_dir], cwd=jdkrepo)
    mx.log('Removing old files from HG...')
    for m in graal_modules:
        m_src_dir = join('src', m.name)
        out = run_output(['hg', 'status', '-dn', m_src_dir], cwd=jdkrepo)
        if out:
            mx.run(['hg', 'rm'] + out.split(), cwd=jdkrepo)

    out = run_output(['git', 'tag', '-l', 'JDK-*'], cwd=mx_compiler._suite.vc_dir)
    last_jdk_tag = sorted(out.split(), reverse=True)[0]

    pretty = args.pretty or 'format:%h %ad %>(20) %an %s'
    out = run_output(['git', '--no-pager', 'log', '--merges', '--abbrev-commit', '--pretty=' + pretty, '--first-parent', '-r', last_jdk_tag + '..HEAD'] +
            copied_source_dirs, cwd=mx_compiler._suite.vc_dir)
    changes_file = 'changes-since-{}.txt'.format(last_jdk_tag)
    with open(changes_file, 'w') as fp:
        fp.write(out)
    mx.log('Saved changes since {} to {}'.format(last_jdk_tag, os.path.abspath(changes_file)))
    if overwritten:
        overwritten_file = 'overwritten-diffs.txt'
        with open(overwritten_file, 'w') as fp:
            fp.write(overwritten)
        mx.warn('Overwritten changes detected in OpenJDK Graal! See diffs in ' + os.path.abspath(overwritten_file))
