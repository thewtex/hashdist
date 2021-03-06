"""
:mod:`hashdist.core.run_job` --- Job execution in controlled environment
========================================================================

Executes a set of commands in a controlled environment, determined by
a JSON job specification. This is used as the "build" section of ``build.json``,
the "install" section of ``artifact.json``, and so on.

The job spec may not completely specify the job environment because it
is usually a building block of other specs which may imply certain
additional environment variables. E.g., during a build, ``$ARTIFACT``
and ``$BUILD`` are defined even if they are never mentioned here.


Job specification
-----------------

The job spec is a document that contains what's needed to set up a
controlled environment and run the commands. The idea is to be able
to reproduce a job run, and hash the job spec. Example:

.. code-block:: python
    
    {
        "import" : [
            {"ref": "BASH", "id": "virtual:bash"},
            {"ref": "MAKE", "id": "virtual:gnu-make/3+"},
            {"ref": "ZLIB", "id": "zlib/2d4kh7hw4uvml67q7npltyaau5xmn4pc"},
            {"ref": "UNIX", "id": "virtual:unix"},
            {"ref": "GCC", "id": "gcc/jonykztnjeqm7bxurpjuttsprphbooqt"}
         ],
         "commands" : [
             {"chdir": "src"},
             {"prepend_path": "FOOPATH", "value": "$ARTIFACT/bin"},
             {"set": "INCLUDE_FROB", "value": "0"},
             {"cmd": ["pkg-config", "--cflags", "foo"], "to_var": "CFLAGS"},
             {"cmd": ["./configure", "--prefix=$ARTIFACT", "--foo-setting=$FOO"]}
             {"cmd": ["bash", "$in0"],
              "inputs": [
                  {"text": [
                      "[\"$RUN_FOO\" != \"\" ] && ./foo"
                      "make",
                      "make install"
                  ]}
             }
         ],
    }


      
Job spec root node
------------------

The root node is also a command node, as described below, but has two
extra allowed keys:

**import**:
    The artifacts needed in the environment for the run. After the
    job has run they have no effect (i.e., they do not
    affect garbage collection or run-time dependencies of a build,
    for instance). The list is ordered and earlier entries are imported
    before latter ones.

    * **id**: The artifact ID. If the value is prepended with
      ``"virtual:"``, the ID is a virtual ID, used so that the real
      one does not contribute to the hash. See section on virtual
      imports below.

    * **ref**: A name to use to inject information of this dependency
      into the environment. Above, ``$ZLIB_DIR`` will be the
      absolute path to the ``zlib`` artifact, and ``$ZLIB_ID`` will be
      the full artifact ID. This can be set to `None` in order to not
      set any environment variables for the artifact.

When executing, the environment is set up as follows:

    * Environment is cleared (``os.environ`` has no effect)
    * The initial environment provided by caller (e.g.,
      :class:`.BuildStore` provides `$ARTIFACT` and `$BUILD`) is loaded
    * The `import` section is processed
    * Commands executed (which may modify env)

Command node
------------

The command nodes is essentially a script language, but lacks any form
of control flow. The purpose is to control the environment, and then
quickly dispatch to a script in a real programming language.

Also, the overall flow of commands to set up the build environment are
typically generated by a pipeline from a package definition, and
generating a text script in a pipeline is no fun.

See example above for basic script structure. Rules:

 * Every item in the job is either a `cmd` or a `commands` or a `hit`, i.e.
   those keys are mutually exclusive and defines the node type.

 * `commands`: Push a new environment and current directory to stack,
   execute sub-commands, and pop the stack.

 * `cmd`: The list is passed straight to :func:`subprocess.Popen` as is
   (after variable substitution). I.e., no quoting, no globbing.

 * `hit`: executes the `hit` tool *in-process*. It acts like `cmd` otherwise,
   e.g., `to_var` works.

 * `chdir`: Change current directory, relative to current one (same as modifying `PWD`
   environment variable)

 * `set`, `prepend/append_path`, `prepend/append_flag`: Change environment
   variables, inserting the value specified by the `value` key, using
   variable substitution as explained below. `set` simply overwrites
   variable, while the others modify path/flag-style variables, using the
   `os.path.patsep` for `prepend/append_path` and a space for `prepend/append_flag`.
   **NOTE:** One can use `nohash_value` instead of `value` to avoid the
   value to enter the hash of a build specification.

 * `files` specifies files that are dumped to temporary files and made available
   as `$in0`, `$in1` and so on. Each file has the form ``{typestr: value}``,
   where `typestr` means:
   
       * ``text``: `value` should be a list of strings which are joined by newlines
       * ``string``: `value` is dumped verbatim to file
       * ``json``: `value` is any JSON document, which is serialized to the file

 * stdout and stderr will be logged, except if `to_var` or
   `append_to_file` is present in which case the stdout is capture to
   an environment variable or redirected in append-mode to file, respectively. (In
   the former case, the resulting string undergoes `strip()`, and is
   then available for the following commands within the same scope.)

 * Variable substitution is performed the following places: The `cmd`,
   `value` of `set` etc., `chdir` argument, `stdout_to_file`.  The syntax is
   ``$CFLAGS`` and ``${CFLAGS}``. ``\$`` is an escape for ``$``,
   ``\\`` is an escape for ``\``, other escapes not currently supported
   and ``\`` will carry through unmodified.


For the `hit` tool, in addition to what is listed in ``hit
--help``, the following special command is available for interacting
with the job runner:

 * ``hit logpipe HEADING LEVEL``: Creates a new Unix FIFO and prints
   its name to standard output (it will be removed once the job
   terminates). The job runner will poll the pipe and print
   anything written to it nicely formatted to the log with the given
   heading and log level (the latter is one of ``DEBUG``, ``INFO``,
   ``WARNING``, ``ERROR``).

.. note::

    ``hit`` is not automatically available in the environment in general
    (in launched scripts etc.), for that, see :mod:`hashdist.core.hit_recipe`.
    ``hit logpipe`` is currently not supported outside of the job spec
    at all (this could be supported through RPC with the job runner, but the
    gain seems very slight).




Virtual imports
---------------

Some times it is not desirable for some imports to become part of the hash.
For instance, if the ``cp`` tool is used in the job, one is normally
ready to trust that the result wouldn't have been different if a newer
version of the ``cp`` tool was used instead.

Virtual imports, such as ``virtual:unix`` in the example above, are
used so that the hash depends on a user-defined string rather than the
artifact contents. If a bug in ``cp`` is indeed discovered, one can
change the user-defined string (e.g, ``virtual:unix/r2``) in order to
change the hash of the job desc.

.. note::
   One should think about virtual dependencies merely as a tool that gives
   the user control (and responsibility) over when the hash should change.
   They are *not* the primary mechanism for providing software
   from the host; though software from the host will sometimes be
   specified as virtual dependencies.

Reference
---------

"""

import sys
import os
import fcntl
from os.path import join as pjoin
import shutil
import subprocess
from glob import glob
from string import Template
from pprint import pformat
import tempfile
import errno
import select
from StringIO import StringIO
import json
from pprint import pprint

from ..hdist_logging import CRITICAL, ERROR, WARNING, INFO, DEBUG

from .common import working_directory

LOG_PIPE_BUFSIZE = 4096


class InvalidJobSpecError(ValueError):
    pass

class JobFailedError(RuntimeError):
    pass


# Utils
def substitute(logger, x, env):
    try:
        return substitute(x, env)
    except KeyError, e:
        msg = 'No such environment variable: %s' % str(e)
        logger.error(msg)
        raise ValueError(msg)

def handle_imports(logger, build_store, artifact_dir, virtuals, job_spec):
    """Sets up environment variables for a job. This includes $MYIMPORT_DIR, $MYIMPORT_ID,
    $ARTIFACT, $HDIST_IMPORT, $HDIST_IMPORT_PATHS.

    Returns
    -------

    env : dict
        Environment containing HDIST_IMPORT{,_PATHS} and variables for each import.
    script : list
        Instructions to execute; imports first and the job_spec commands afterwards.
    """
    job_spec = canonicalize_job_spec(job_spec)

    imports = job_spec['import']
    result = []
    env = {}
    HDIST_IMPORT = []
    HDIST_IMPORT_PATHS = []
    
    for import_ in imports:
        dep_id = import_['id']
        dep_ref = import_['ref'] if 'ref' in import_ else None
        # Resolutions of virtual imports should be provided by the user
        # at the time of build
        if dep_id.startswith('virtual:'):
            try:
                dep_id = virtuals[dep_id]
            except KeyError:
                raise ValueError('build spec contained a virtual dependency "%s" that was not '
                                 'provided' % dep_id)

        dep_dir = build_store.resolve(dep_id)
        if dep_dir is None:
            raise InvalidJobSpecError('Dependency "%s"="%s" not already built, please build it first' %
                                        (dep_ref, dep_id))

        HDIST_IMPORT.append(dep_id)
        HDIST_IMPORT_PATHS.append(dep_dir)
        if dep_ref is not None:
            env['%s_DIR' % dep_ref] = dep_dir
            env['%s_ID' % dep_ref] = dep_id

    result.append({'set': 'ARTIFACT', 'value': artifact_dir})
    result.extend(job_spec['commands'])
    env['HDIST_IMPORT'] = ' '.join(HDIST_IMPORT)
    env['HDIST_IMPORT_PATHS'] = os.path.pathsep.join(HDIST_IMPORT_PATHS)
    return env, result

def run_job(logger, build_store, job_spec, override_env, artifact_dir, virtuals, cwd, config,
            temp_dir=None, debug=False):
    """Runs a job in a controlled environment, according to rules documented above.

    Parameters
    ----------

    logger : Logger

    build_store : BuildStore
        BuildStore to find referenced artifacts in.

    job_spec : document
        See above

    override_env : dict
        Extra environment variables not present in job_spec, these will be added
        last and overwrite existing ones.

    artifact_dir : str
        The value $ARTIFACT should take after running the imports

    virtuals : dict
        Maps virtual artifact to real artifact IDs.

    cwd : str
        The starting working directory of the job. Currently this
        cannot be changed (though a ``cd`` command may be implemented in
        the future if necesarry)

    config : dict
        Configuration from :mod:`hashdist.core.config`. This will be
        serialied and put into the HDIST_CONFIG environment variable
        for use by ``hit``.

    temp_dir : str (optional)
        A temporary directory for use by the job runner. Files will be left in the
        dir after execution.

    debug : bool
        Whether to run in debug mode.

    Returns
    -------

    out_env: dict
        The environment after the last command that was run (regardless
        of scoping/nesting). If the job spec is empty (no commands),
        this will be an empty dict.

    """
    env, assembled_commands = handle_imports(logger, build_store, artifact_dir, virtuals, job_spec)

    if 'commands' not in job_spec:
        # Wait until here with exiting because we still want to err if imports are not built
        return {}

    # Need to explicitly clear PATH, otherwise Popen will set it.
    env['PATH'] = ''
    env.update(override_env)
    env['HDIST_VIRTUALS'] = pack_virtuals_envvar(virtuals)
    env['HDIST_CONFIG'] = json.dumps(config, separators=(',', ':'))
    env['PWD'] = os.path.abspath(cwd)
    executor = CommandTreeExecution(logger, temp_dir, debug=debug)
    try:
        executor.run_command_list(assembled_commands, env, ())
    finally:
        executor.close()
    return executor.last_env

def canonicalize_job_spec(job_spec):
    """Returns a copy of job_spec with default values filled in.

    Also performs a tiny bit of validation.
    """
    def canonicalize_import(item):
        item = dict(item)
        if item.setdefault('ref', None) == '':
            raise ValueError('Empty ref should be None, not ""')
        return item

    result = dict(job_spec)
    result['import'] = [
        canonicalize_import(item) for item in result.get('import', ())]
    return result
    
def substitute(x, env):
    """
    Substitute environment variable into a string following the rules
    documented above.

    Raises KeyError if an unreferenced variable is not present in env
    (``$$`` always raises KeyError)
    """
    if '$$' in x:
        # it's the escape character of string.Template, hence the special case
        raise KeyError('$$ is not allowed (no variable can be named $): %s' % x)
    x = x.replace(r'\\\\', r'\\')
    x = x.replace(r'\$', r'$$')
    return Template(x).substitute(env)

def pack_virtuals_envvar(virtuals):
    return ';'.join('%s=%s' % tup for tup in sorted(virtuals.items()))

def unpack_virtuals_envvar(x):
    if not x:
        return {}
    else:
        return dict(tuple(tup.split('=')) for tup in x.split(';'))

class CommandTreeExecution(object):
    """
    Class for maintaining state (in particular logging pipes) while
    executing script. Note that the environment is passed around as
    parameters instead.

    Executing :meth:`run` multiple times amounts to executing
    different variable scopes (but with same logging pipes set up).
    
    Parameters
    ----------

    logger : Logger

    rpc_dir : str
        A temporary directory on a local filesystem. Currently used for creating
        pipes with the "hit logpipe" command.
    """

    def __init__(self, logger, temp_dir=None, debug=False, debug_shell='/bin/bash'):
        self.debug = debug
        self.debug_shell = debug_shell # todo: pass this in from outside
        self.logger = logger
        self.log_fifo_filenames = {}
        if temp_dir is None:
            self.rm_temp_dir = True
            temp_dir = os.path.realpath(tempfile.mkdtemp(prefix='hashdist-run-job-'))
        else:
            if os.listdir(temp_dir) != []:
                raise Exception('temp_dir must be an empty directory')
            self.rm_temp_dir = False
        self.temp_dir = temp_dir
        self.last_env = None

    def close(self):
        """Removes log FIFOs; should always be called when one is done
        """
        if self.rm_temp_dir:
            shutil.rmtree(self.temp_dir)

    def substitute(self, x, env):
        try:
            return substitute(x, env)
        except KeyError, e:
            msg = 'No such environment variable: %s' % str(e)
            self.logger.error(msg)
            raise ValueError(msg)

    def dump_inputs(self, inputs, node_pos):
        """
        Handles the 'inputs' attribute of a node by dumping to temporary files.

        Returns
        -------

        A dict with environment variables that can be used to update `env`,
        containing ``$in0``, ...
        """
        env = {}
        for i, input in enumerate(inputs):
            if not isinstance(input, dict):
                raise TypeError("input entries should be dict")
            name = 'in%d' % i
            filename = '_'.join(str(x) for x in node_pos) + '_' + name
            filename = pjoin(self.temp_dir, filename)

            if sum(['text' in input, 'json' in input, 'string' in input]) != 1:
                raise ValueError("Need exactly one of 'text', 'json', 'string' in %r" % input)
            if 'text' in input:
                value = '\n'.join(input['text'])
            elif 'string' in input:
                value = input['string']
            elif 'json' in input:
                value = json.dumps(input['json'], indent=4)
                filename += '.json'
            else:
                assert False

            with open(filename, 'w') as f:
                f.write(value)
            env[name] = filename
        return env

    def run_node(self, node, env, node_pos):
        """Executes a script node and its children

        Parameters
        ----------
        node : dict
            A command node

        env : dict
            The environment (will be modified). The PWD variable tracks working directory
            and should always be set on input.

        node_pos : tuple
            Tuple of the "path" to this command node; e.g., (0, 1) for second
            command in first group.
        """
        type_keys = ['commands', 'cmd', 'hit', 'set', 'prepend_path', 'append_path',
                     'prepend_flag', 'append_flag', 'chdir']
        type = None
        for t in type_keys:
            if t in node:
                if type is not None:
                    msg = 'Several action types present: %s and %s' % (type, t)
                    self.logger.error(msg)
                    raise InvalidJobSpecError(msg)
                type = t
        if type is None and len(node) > 0:
            msg = 'Node must be empty or have one of the keys %s' % ', '.join(type_keys)
            self.logger.error(msg)
            raise InvalidJobSpecError(msg)
        elif len(node) > 0:
            getattr(self, 'handle_%s' % type)(node, env, node_pos)

    def handle_chdir(self, node, env, node_pos):
        d = self.substitute(node['chdir'], env)
        env['PWD'] = os.path.abspath(pjoin(env['PWD'], d))

    def handle_set(self, node, env, node_pos):
        self.handle_env_mod(node, env, node_pos, node['set'], 'set', None)

    def handle_append_path(self, node, env, node_pos):
        self.handle_env_mod(node, env, node_pos,
                            node['append_path'], 'append', os.path.pathsep)

    def handle_prepend_path(self, node, env, node_pos):
        self.handle_env_mod(node, env, node_pos,
                            node['prepend_path'], 'prepend', os.path.pathsep)

    def handle_append_flag(self, node, env, node_pos):
        self.handle_env_mod(node, env, node_pos,
                            node['append_flag'], 'append', ' ')
    
    def handle_prepend_flag(self, node, env, node_pos):
        self.handle_env_mod(node, env, node_pos,
                            node['prepend_flag'], 'prepend', ' ')

    def handle_env_mod(self, node, env, node_pos, varname, action, sep):
        value = node.get('nohash_value', None)
        if value is None:
            value = node['value']
        value = self.substitute(value, env)
        if action == 'set' or varname not in env or len(env[varname]) == 0:
            env[varname] = value
        elif action == 'prepend':
            env[varname] = sep.join([value, env[varname]])
        elif action == 'append':
            env[varname] = sep.join([env[varname], value])
        else:
            assert False

    def handle_cmd(self, node, env, node_pos):
        self.handle_command_nodes(node, env, node_pos)

    def handle_hit(self, node, env, node_pos):
        self.handle_command_nodes(node, env, node_pos)

    def handle_command_nodes(self, node, env, node_pos):
        if not isinstance(node, dict):
            raise TypeError('command node must be a dict; got %r' % node)
        if sum(['cmd' in node, 'hit' in node, 'commands' in node, 'set' in node]) != 1:
            raise ValueError("Each script node should have exactly one of the 'cmd', 'hit', 'commands' keys")
        if sum(['to_var' in node, 'stdout_to_file' in node]) > 1:
            raise ValueError("Can only have one of to_var, stdout_to_file")
        if 'commands' in node and ('append_to_file' in node or 'to_var' in node or 'inputs' in node):
            raise ValueError('"commands" not compatible with to_var or append_to_file or inputs')


        # Make scopes
        node_env = dict(env)

        if 'cmd' in node or 'hit' in node:
            inputs = node.get('inputs', ())
            node_env.update(self.dump_inputs(inputs, node_pos))
            if 'cmd' in node:
                key = 'cmd'
                args = node['cmd']
                func = self.run_cmd
                debug_func = self.debug_call
            else:
                key = 'hit'
                args = node['hit']
                func = self.run_hit
                debug_func = func
            if not isinstance(args, list):
                raise TypeError("'%s' arguments must be a list, got %r" % (key, args))
            args = [self.substitute(x, node_env) for x in args]

            if 'to_var' in node:
                stdout = StringIO()
                func(args, node_env, stdout_to=stdout)
                # modifying env, not node_env, to export change
                env[node['to_var']] = stdout.getvalue().strip()

            elif 'append_to_file' in node:
                stdout_filename = self.substitute(node['append_to_file'], node_env)
                if not os.path.isabs(stdout_filename):
                    stdout_filename = pjoin(env['PWD'], stdout_filename)
                stdout_filename = os.path.realpath(stdout_filename)
                if stdout_filename.startswith(self.temp_dir):
                    raise NotImplementedError("Cannot currently use stream re-direction to write to "
                                              "a log-pipe (doing the write from a "
                                              "sub-process is OK)")
                with file(stdout_filename, 'a') as stdout:
                    func(args, node_env, stdout_to=stdout)

            else:
                # does not capture output, so we may decide to debug instead;
                # debug is not possible when capturing output (until that mechanism
                # is changed...)
                if self.debug:
                    debug_func(args, node_env)
                else:
                    func(args, node_env)
        else:
            assert False

        self.last_env = dict(node_env)

    def handle_commands(self, node, env, node_pos):
        sub_env = dict(env)
        self.run_command_list(node['commands'], sub_env, node_pos)

    def run_command_list(self, commands, env, node_pos):
        for i, command_node in enumerate(commands):
            pos = node_pos + (i,)
            self.run_node(command_node, env, pos)

    def run_cmd(self, args, env, stdout_to=None):
        logger = self.logger
        logger.debug('running %r' % args)
        logger.debug('environment:')
        for line in pformat(env).splitlines():
            logger.debug('  ' + line)
        try:
            self.logged_check_call(args, env, stdout_to)
        except subprocess.CalledProcessError, e:
            logger.error("command failed (code=%d); raising" % e.returncode)
            raise

    def run_hit(self, args, env, stdout_to=None):
        args = ['hit'] + args
        logger = self.logger
        logger.debug('running %r' % args)
        # run it in the same process, but do not emit
        # INFO-messages from sub-command unless level is DEBUG
        old_level = logger.level
        old_stdout = sys.stdout
        try:
            if logger.level > DEBUG:
                logger.level = WARNING
            if stdout_to is not None:
                sys.stdout = stdout_to

            if len(args) >= 2 and args[1] == 'logpipe':
                if len(args) != 4:
                    raise ValueError('wrong number of arguments to "hit logpipe"')
                sublogger_name, level = args[2:]
                self.create_log_pipe(sublogger_name, level)
            else:
                from ..cli.main import command_line_entry_point
                with working_directory(env['PWD']):
                    retcode = command_line_entry_point(args, env, logger)
                if retcode != 0:
                    raise RuntimeError("hit command failed with code: %d" % ret)
        except SystemExit as e:
            logger.error("hit command failed with code: %d" % e.code)
            raise
        except Exception as e:
            logger.error("hit command failed: %s" % str(e))
            raise
        finally:
            logger.level = old_level
            sys.stdout = old_stdout

    def debug_call(self, args, env):
        env = dict(env)
        # leak PS1 from os environment, but prepend our message
        env['PS1'] = '[HASHDIST DEBUG] %s' % os.environ.get('PS1', '')
        # Create temporary file for env used by bash
        tmpdir = tempfile.mkdtemp()
        try:
            rcfile = pjoin(tmpdir, 'env')
            with open(rcfile, 'w') as f:
                for key, value in env.iteritems():
                    f.write("export %s='%s'\n" % (key, value))

            with working_directory(env['PWD']):
                sys.stderr.write('Entering Hashdist debug mode. Please execute the following command: \n')
                sys.stderr.write('  %s\n' % args)
                sys.stderr.write('\n')
                sys.stderr.write('When you are done, "exit 1" to abort build, or "exit 0" to continue.\n\n')
                proc = subprocess.Popen([self.debug_shell, '--noprofile', '--rcfile', rcfile])
                retcode = proc.wait()
                if retcode != 0:
                    self.logger.error("Debug build manually aborted")
                    raise RuntimeError("Debug build manually aborted")
        finally:
            shutil.rmtree(tmpdir)

    def logged_check_call(self, args, env, stdout_to):
        """
        Similar to subprocess.check_call, but multiplexes input from stderr, stdout
        and any number of log FIFO pipes available to the called process into
        a single Logger instance. Optionally captures stdout instead of logging it.
        """
        logger = self.logger
        try:
            proc = subprocess.Popen(args,
                                    cwd=env['PWD'],
                                    env=env,
                                    stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    close_fds=True)
        except OSError, e:
            if e.errno == errno.ENOENT:
                # fix error message up a bit since the situation is so confusing
                if '/' in args[0]:
                    msg = 'command "%s" not found (cwd: %s)' % (args[0], env['PWD'])
                else:
                    msg = 'command "%s" not found in $PATH (cwd: %s)' % (args[0], env['PWD'])
                logger.error(msg)
                raise OSError(e.errno, msg)
            else:
                raise

        if 'linux' in sys.platform and not _TEST_LOG_PROCESS_SIMPLE:
            retcode = self._log_process_with_logpipes(proc, stdout_to)
        else:
            if len(self.log_fifo_filenames) > 0:
                raise NotImplementedError('log pipes not implemented on this platform')
            retcode = self._log_process_simple(proc, stdout_to)

        if retcode != 0:
            exc = subprocess.CalledProcessError(retcode, args)
            self.logger.error(str(exc))
            raise exc

    def _log_process_simple(self, proc, stdout_to):
        logger = self.logger
        stdout_fd, stderr_fd = proc.stdout.fileno(), proc.stderr.fileno()
        fds = [stdout_fd, stderr_fd]
        encoding = sys.stderr.encoding
        for fd in fds: # set O_NONBLOCK
            fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) | os.O_NONBLOCK)

        buffers = {stdout_fd: '', stderr_fd: ''}
        while True:
            select.select(fds, [], [], 0.05)
            for fd in fds:
                try:
                    s = os.read(fd, LOG_PIPE_BUFSIZE)
                except IOError, e:
                    if e.errno != errno.EAGAIN:
                        raise
                    s = ''
                except OSError, e:
                    if e.errno != errno.EAGAIN:
                        raise
                    s = ''
                if s != '':
                    if stdout_to is not None and fd == stdout_fd:
                        # Just forward
                        stdout_to.write(s)
                    else:
                        buffers[fd] += s
                        lines = buffers[fd].splitlines(True) # keepends=True
                        if lines[-1][-1] != '\n':
                            buffers[fd] = lines[-1]
                            del lines[-1]
                        else:
                            buffers[fd] = ''
                        for line in lines:
                            if line[-1] == '\n':
                                line = line[:-1]
                            if encoding:
                                logger.debug(line.decode(encoding))
                            else:
                                logger.debug(line)
            if proc.poll() is not None:
                break
        for buf in buffers.values():
            if buf != '':
                logger.debug(buf)
        return proc.wait()

    def _log_process_with_logpipes(self, proc, stdout_to):
        # Weave together input from stdout, stderr, and any attached log
        # pipes.  To avoid any deadlocks with unbuffered stderr
        # interlaced with use of log pipe etc. we avoid readline(), but
        # instead use os.open to read and handle line-assembly ourselves...
        logger = self.logger
        
        stdout_fd, stderr_fd = proc.stdout.fileno(), proc.stderr.fileno()
        poller = select.poll()
        poller.register(stdout_fd)
        poller.register(stderr_fd)

        # Set up { fd : (logger, level) }
        loggers = {stdout_fd: (logger, DEBUG), stderr_fd: (logger, DEBUG)}
        buffers = {stdout_fd: '', stderr_fd: ''}

        # The FIFO pipes are a bit tricky as they need to the re-opened whenever
        # any client closes. This also modified the loggers dict and fd_to_logpipe
        # dict.

        fd_to_logpipe = {} # stderr/stdout not re-opened
        
        def open_fifo(fifo_filename, logger, level):
            # need to open in non-blocking mode to avoid waiting for printing client process
            fd = os.open(fifo_filename, os.O_NONBLOCK|os.O_RDONLY)
            # remove non-blocking after open to treat all streams uniformly in
            # the reading code
            fcntl.fcntl(fd, fcntl.F_SETFL, os.O_RDONLY)
            loggers[fd] = (logger, level)
            buffers[fd] = ''
            fd_to_logpipe[fd] = fifo_filename
            poller.register(fd)

        def flush_buffer(fd):
            buf = buffers[fd]
            if buf:
                # flush buffer in case last line not terminated by '\n'
                sublogger, level = loggers[fd]
                sublogger.log(level, buf)
            del buffers[fd]

        def close_fifo(fd):
            flush_buffer(fd)
            poller.unregister(fd)
            os.close(fd)
            del loggers[fd]
            del fd_to_logpipe[fd]
            
        def reopen_fifo(fd):
            fifo_filename = fd_to_logpipe[fd]
            logger, level = loggers[fd]
            close_fifo(fd)
            open_fifo(fifo_filename, logger, level)

        for (header, level), fifo_filename in self.log_fifo_filenames.items():
            sublogger = logger.get_sub_logger(header)
            open_fifo(fifo_filename, sublogger, level)
            
        while True:
            # Python poll() doesn't return when SIGCHLD is received;
            # and there's the freak case where a process first
            # terminates stdout/stderr, then trying to write to a log
            # pipe, so we should track child termination the proper
            # way. Being in Python, it's easiest to just poll every
            # 50 ms; the majority of the time is spent in poll() so
            # it doesn't really increase log message latency
            events = poller.poll(50)
            if len(events) == 0:
                if proc.poll() is not None:
                    break # child terminated
            for fd, reason in events:
                if reason & select.POLLHUP and not (reason & select.POLLIN):
                    # we want to continue receiving PULLHUP|POLLIN until all
                    # is read
                    if fd in fd_to_logpipe:
                        reopen_fifo(fd)
                    elif fd in (stdout_fd, stderr_fd):
                        poller.unregister(fd)
                elif reason & select.POLLIN:
                    if stdout_to is not None and fd == stdout_fd:
                        # Just forward
                        buf = os.read(fd, LOG_PIPE_BUFSIZE)
                        stdout_to.write(buf)
                    else:
                        # append new bytes to what's already been read on this fd; and
                        # emit any completed lines
                        new_bytes = os.read(fd, LOG_PIPE_BUFSIZE)
                        assert new_bytes != '' # after all, we did poll
                        buffers[fd] += new_bytes
                        lines = buffers[fd].splitlines(True) # keepends=True
                        if lines[-1][-1] != '\n':
                            buffers[fd] = lines[-1]
                            del lines[-1]
                        else:
                            buffers[fd] = ''
                        # have list of lines, emit them to logger
                        sublogger, level = loggers[fd]
                        for line in lines:
                            if line[-1] == '\n':
                                line = line[:-1]
                            sublogger.log(level, line)

        flush_buffer(stderr_fd)
        flush_buffer(stdout_fd)
        for fd in fd_to_logpipe.keys():
            close_fifo(fd)

        retcode = proc.wait()
        return retcode

    def create_log_pipe(self, sublogger_name, level_str):
        level = dict(CRITICAL=CRITICAL, ERROR=ERROR, WARNING=WARNING, INFO=INFO, DEBUG=DEBUG)[level_str]
        fifo_filename = self.log_fifo_filenames.get((sublogger_name, level), None)
        if fifo_filename is None:
            fifo_filename = pjoin(self.temp_dir, "logpipe-%s-%s" % (sublogger_name, level_str))
            os.mkfifo(fifo_filename, 0600)
            self.log_fifo_filenames[sublogger_name, level] = fifo_filename
        sys.stdout.write(fifo_filename)

# temporarily set by test_run_job; can also set manually to emulate OS X
_TEST_LOG_PROCESS_SIMPLE = False
