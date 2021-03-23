# -------------------------------------------------------------------------
#
#  Part of the CodeChecker project, under the Apache License v2.0 with
#  LLVM Exceptions. See LICENSE for license information.
#  SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# -------------------------------------------------------------------------
"""
List the checkers available in the analyzers.
"""


import argparse
import os
import subprocess
import sys
from collections import defaultdict

from codechecker_analyzer import analyzer_context
from codechecker_analyzer.analyzers import analyzer_types
from codechecker_analyzer.analyzers.clangsa.analyzer import ClangSA

from codechecker_common import arg, logger
from codechecker_common.output import USER_FORMATS, twodim
from codechecker_analyzer import env
from codechecker_analyzer.analyzers.config_handler import CheckerState

LOG = logger.get_logger('system')


def get_diagtool_bin():
    """
    Return full path of diagtool.

    Select clang binary, check for a 'diagtool' binary next to the selected
    clang binary and return full path of this binary if it exists.
    """
    context = analyzer_context.get_context()
    clang_bin = context.analyzer_binaries.get(ClangSA.ANALYZER_NAME)

    if not clang_bin:
        return None

    # Resolve symlink.
    clang_bin = os.path.realpath(clang_bin)

    # Find diagtool next to the clang binary.
    diagtool_bin = os.path.join(os.path.dirname(clang_bin), 'diagtool')
    if os.path.exists(diagtool_bin):
        return diagtool_bin

    LOG.debug("'diagtool' can not be found next to the clang binary (%s)!",
              clang_bin)


def get_warnings(env=None):
    """
    Returns list of warning flags by using diagtool.
    """
    diagtool_bin = get_diagtool_bin()

    if not diagtool_bin:
        return []

    try:
        result = subprocess.check_output(
            [diagtool_bin, 'tree'],
            env=env,
            universal_newlines=True,
            encoding="utf-8",
            errors="ignore")
        return [w[2:] for w in result.split() if w.startswith("-W")]
    except (subprocess.CalledProcessError, OSError):
        return []


def get_argparser_ctor_args():
    """
    This method returns a dict containing the kwargs for constructing an
    argparse.ArgumentParser (either directly or as a subparser).
    """

    data_files_dir_path = analyzer_context.get_context().data_files_dir_path
    labels_dir_path = os.path.join(data_files_dir_path, 'config', 'labels')
    return {
        'prog': 'CodeChecker checkers',
        'formatter_class': arg.RawDescriptionDefaultHelpFormatter,

        # Description is shown when the command's help is queried directly
        'description': "Get the list of checkers available and their enabled "
                       "status in the supported analyzers.",

        # Epilogue is shown after the arguments when the help is queried
        # directly.
        'epilog': """
The list of checkers that are enabled or disabled by default can be edited by
editing "profile:default" labels in the directory '{}'.
""".format(os.path.join(labels_dir_path)),

        # Help is shown when the "parent" CodeChecker command lists the
        # individual subcommands.
        'help': "List the checkers available for code analysis."
    }


def add_arguments_to_parser(parser):
    """
    Add the subcommand's arguments to the given argparse.ArgumentParser.
    """

    parser.add_argument('--analyzers',
                        nargs='+',
                        dest='analyzers',
                        metavar='ANALYZER',
                        required=False,
                        choices=analyzer_types.supported_analyzers,
                        default=list(analyzer_types.supported_analyzers.
                                     keys()),
                        help="Show checkers only from the analyzers "
                             "specified.")

    if get_diagtool_bin():
        parser.add_argument('-w', '--warnings',
                            dest='show_warnings',
                            default=argparse.SUPPRESS,
                            action='store_true',
                            required=False,
                            help="Show available warning flags.")

    parser.add_argument('--details',
                        dest='details',
                        default=argparse.SUPPRESS,
                        action='store_true',
                        required=False,
                        help="Show details about the checker, such as "
                             "description, if available.")

    parser.add_argument('--label',
                        nargs='+',
                        required=False,
                        default=argparse.SUPPRESS,
                        help="")

    parser.add_argument('--profile',
                        dest='profile',
                        metavar='PROFILE/list',
                        required=False,
                        default=argparse.SUPPRESS,
                        help="List checkers enabled by the selected profile. "
                             "'list' is a special option showing details "
                             "about profiles collectively.")

    parser.add_argument('--guideline',
                        dest='guideline',
                        nargs='*',
                        required=False,
                        default=None,
                        help="List checkers that report on a specific "
                             "guideline rule. Here you can add the guideline "
                             "name or the ID of a rule. Without additional "
                             "parameter, the available guidelines and their "
                             "corresponding rules will be listed.")

    parser.add_argument('--checker-config',
                        dest='checker_config',
                        default=argparse.SUPPRESS,
                        action='store_true',
                        required=False,
                        help="Show checker configuration options for all "
                             "existing checkers supported by the analyzer. "
                             "These can be given to 'CodeChecker analyze "
                             "--checker-config'.")

    filters = parser.add_mutually_exclusive_group(required=False)

    filters.add_argument('--only-enabled',
                         dest='only_enabled',
                         default=argparse.SUPPRESS,
                         action='store_true',
                         help="Show only the enabled checkers.")

    filters.add_argument('--only-disabled',
                         dest='only_disabled',
                         default=argparse.SUPPRESS,
                         action='store_true',
                         help="Show only the disabled checkers.")

    parser.add_argument('-o', '--output',
                        dest='output_format',
                        required=False,
                        default='rows',
                        choices=USER_FORMATS,
                        help="The format to list the applicable checkers as.")

    logger.add_verbose_arguments(parser)
    parser.set_defaults(func=main)


def main(args):
    """
    List the checkers available in the specified (or all supported) analyzers
    alongside with their description or enabled status in various formats.
    """

    # If the given output format is not 'table', redirect logger's output to
    # the stderr.
    logger.setup_logger(args.verbose if 'verbose' in args else None,
                        None if args.output_format == 'table' else 'stderr')

    context = analyzer_context.get_context()
    working_analyzers, errored = analyzer_types.check_supported_analyzers(
        args.analyzers,
        context)
    analyzer_types.check_available_analyzers(working_analyzers, errored)

    analyzer_environment = env.extend(context.path_env_extra,
                                      context.ld_lib_path_extra)

    analyzer_config_map = analyzer_types.build_config_handlers(
        args, context, working_analyzers)

    def uglify(text):
        """
        csv and json format output contain this non human readable header
        string: no CamelCase and no space.
        """
        return text.lower().replace(' ', '_')

    def match_guideline(checker_name, selected_guidelines):
        """
        Returns True if checker_name gives reports related to any of the
        selected guideline rule.
        checker_name -- A full checker name.
        selected_guidelines -- A list of guideline names or guideline rule IDs.
        """
        labels = context.checker_labels.labels_of_checker(checker_name)
        choices = context.checker_labels.get_description('guideline')

        for label, value in labels:
            if (label == 'guideline' or label in choices) and \
                    value in selected_guidelines:
                return True

        return False

    def format_guideline(guideline):
        """
        Convert guideline rules to human-readable format.
        guideline -- Dictionary in the following format:
                     {"guideline_1": ["rule_1", "rule_2"]}
        """
        return ' '.join('Related {} rules: {}'.format(g, ', '.join(r))
                        for g, r in guideline.items())

    def guideline_rules_for_checker(checker):
        """
        Returns the guideline and rules covered by the given checker. This
        function returns a dict which maps guideline names to the covered
        rules: { "sei-cert": ["arr30-c", ...], ... }.
        """
        result = defaultdict(list)
        labels = context.checker_labels.labels_of_checker(checker)
        guidelines = context.checker_labels.get_description('guideline')

        for label in labels:
            if label[0] in guidelines:
                result[label[0]].append(label[1])

        return result

    cl = context.checker_labels

    # TODO: --profile is a deprecated flag. This section should be removed in
    # the next release.
    # List available checker profiles.
    if 'profile' in args and args.profile == 'list':
        LOG.warning('--profile flag is deprecated and will be removed in the '
                    'next release.')

        if 'details' in args:
            header = ['Profile name', 'Description']
            rows = cl.get_description('profile').items()
        else:
            header = ['Profile name']
            rows = [(key,) for key in cl.get_description('profile')]

        if args.output_format in ['csv', 'json']:
            header = list(map(uglify, header))

        print(twodim.to_str(args.output_format, header, rows))
        return

    # List checker config options.
    if 'checker_config' in args:
        if 'details' in args:
            header = ['Option', 'Description']
        else:
            header = ['Option']

        if args.output_format in ['csv', 'json']:
            header = list(map(uglify, header))

        rows = []
        analyzer_failures = []
        for analyzer in working_analyzers:
            config_handler = analyzer_config_map.get(analyzer)
            analyzer_class = analyzer_types.supported_analyzers[analyzer]

            configs = analyzer_class.get_checker_config(config_handler,
                                                        analyzer_environment)
            if not configs:
                analyzer_failures.append(analyzer)
                continue

            rows.extend((':'.join((analyzer, c[0])), c[1]) if 'details' in args
                        else (':'.join((analyzer, c[0])),) for c in configs)

        if rows:
            print(twodim.to_str(args.output_format, header, rows))

        if analyzer_failures:
            LOG.error("Failed to get checker configuration options for '%s' "
                      "analyzer(s)! Please try to upgrade your analyzer "
                      "version to use this feature.",
                      ', '.join(analyzer_failures))
            sys.exit(1)

        return

    if args.guideline is not None and len(args.guideline) == 0:
        result = {}

        for guideline in cl.get_description('guideline'):
            result[guideline] = set(cl.occurring_values(guideline))

        header = ['Guideline', 'Rules']
        if args.output_format in ['csv', 'json']:
            header = list(map(uglify, header))

        if args.output_format == 'json':
            rows = [(g, sorted(list(r))) for g, r in result.items()]
        else:
            rows = [(g, ', '.join(sorted(r))) for g, r in result.items()]

        if args.output_format == 'rows':
            for row in rows:
                print('Guideline: {}'.format(row[0]))
                print('Rules: {}'.format(row[1]))
        else:
            print(twodim.to_str(args.output_format, header, rows))
        return

    # List available checkers.
    if 'details' in args:
        header = ['Enabled', 'Name', 'Analyzer', 'Severity', 'Guideline',
                  'Description']
    else:
        header = ['Name']

    if args.output_format in ['csv', 'json']:
        header = list(map(uglify, header))

    rows = []
    for analyzer in working_analyzers:
        config_handler = analyzer_config_map.get(analyzer)
        analyzer_class = analyzer_types.supported_analyzers[analyzer]

        checkers = analyzer_class.get_analyzer_checkers(config_handler,
                                                        analyzer_environment)

        profile_checkers = []
        if 'profile' in args:
            available_profiles = cl.get_description('profile')

            if args.profile not in available_profiles:
                LOG.error("Checker profile '%s' does not exist!",
                          args.profile)
                LOG.error("To list available profiles, use '--profile list'.")
                sys.exit(1)

            profile_checkers = [('profile:' + args.profile, True)]

        config_handler.initialize_checkers(context,
                                           checkers,
                                           profile_checkers)

        for checker, value in config_handler.checks().items():
            state, description = value

            if state != CheckerState.enabled and 'profile' in args:
                continue

            if state == CheckerState.enabled and 'only_disabled' in args:
                continue
            elif state != CheckerState.enabled and 'only_enabled' in args:
                continue

            if args.output_format == 'json':
                state = state == CheckerState.enabled
            else:
                state = '+' if state == CheckerState.enabled else '-'

            if args.guideline is not None:
                if not match_guideline(checker, args.guideline):
                    continue

            if 'details' in args:
                severity = cl.severity(checker)
                guideline = guideline_rules_for_checker(checker)
                if args.output_format != 'json':
                    guideline = format_guideline(guideline)
                rows.append([state, checker, analyzer,
                             severity, guideline, description])
            else:
                rows.append([checker])

    if 'show_warnings' in args:
        for warning in get_warnings(analyzer_environment):
            warning = 'clang-diagnostic-' + warning

            if args.guideline is not None:
                if not match_guideline(warning, args.guideline):
                    continue

            guideline = guideline_rules_for_checker(warning)
            if args.output_format != 'json':
                guideline = format_guideline(guideline)

            if 'details' in args:
                rows.append(['', warning, '-', 'MEDIUM', guideline, '-'])
            else:
                rows.append([warning])

    if rows:
        print(twodim.to_str(args.output_format, header, rows))

    analyzer_types.print_unsupported_analyzers(errored)
