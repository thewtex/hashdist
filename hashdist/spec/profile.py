"""

Not supported:

 - Diamond inheritance

"""

import tempfile
import os
import shutil
from os.path import join as pjoin

from .marked_yaml import marked_yaml_load
from .utils import substitute_profile_parameters
from .. import core

class ConflictingProfilesError(Exception):
    pass

class FileNotFoundError(Exception):
    def __init__(self, msg, relname):
        Exception.__init__(self, msg)
        self.relname = relname

class FileResolver(object):
    """
    Represents a tree of directories containing profile information.
    Is used to resolve which file to load things from.
    """
    def __init__(self, children):
        self.children = children


class Profile(object):
    """

    Profiles acts as nodes in a tree, with `extends` containing the
    parent profiles (which are child nodes in a DAG).
    """
    def __init__(self, basedir, doc_name, doc, extends, rm_on_close=False):
        self.basedir = basedir
        self.doc_name = doc_name
        self.doc = doc
        self.extends = extends
        self.rm_on_close = rm_on_close
        # for now, we require that bases have non-overlapping parameter keys
        self.parameters = {}
        for base in extends:
            for k, v in base.parameters.iteritems():
                if k in self.parameters:
                    raise ConflictingProfilesError('two base profiles set same parameter %s' % k)
                self.parameters[k] = v
        self.parameters.update(doc.get('parameters', {}).get('global', {}))

    def close(self):
        for base in self.extends:
            base.close()
        if self.rm_on_close:
            shutil.rmtree(self.basedir)

    def find_file(self, relname):
        path = pjoin(self.basedir, relname)
        if not os.path.exists(path):
            path = None
            for base in self.extends:
                path_in_base = base.find_file(relname)
                if path_in_base is not None:
                    if path is not None:
                        raise ConflictingProfilesError('file %s found in two different base profiles' % relname)
                    path = path_in_base
        return path

    def get_python_path(self, path=None):
        """
        Constructs a list that can be inserted into sys.path to make
        .py-files in the base subdirectory of this profile and any
        base-profile available.
        """
        if path is None:
            path = []
        for base in self.extends:
            base.get_python_path(path)
        path.insert(0, pjoin(self.basedir, 'base'))
        return path

    def get_packages(self):
        """
        Returns a dict of package includeded in the profile, including
        processing of package specs by base profiles.

        The key is the 'virtual' name of the package within the
        profile. The value is a tuple ``(name, variant)``, where
        variant is `None` if none is given. As a special case,
        'package/skip' removes a package from the dict (which may have
        been added by an ancestor profile).
        """
        def parse_entry(s):
            parts = s.split('/')
            if len(parts) == 1:
                return (parts[0], None)
            elif len(parts) == 2:
                return tuple(parts)
            else:
                raise ValueError('Too many slashes in package name: %s' % s)

        packages = {}
        # import from base
        for base in self.extends:
            for k, v in base.get_packages().iteritems():
                if k in packages:
                    raise ConflictingProfilesError('package %s found in two different base profiles')
                packages[k] = v
        # parse this profiles packages section
        lst = self.doc.get('packages', [])
        for entry in lst:
            if isinstance(entry, basestring):
                name, variant = parse_entry(entry)
                vname = name
            elif len(entry) != 1:
                raise ValueError('each package specification dict should have a single key only')
            else:
                vname, s = entry.items()[0]
                name, variant = parse_entry(s)

            if variant == 'skip':
                if vname in packages:
                    del packages[vname]
                continue
            packages[vname] = (name, variant)

        return packages

    def __repr__(self):
        return '<Profile %s>' % pjoin(self.basedir, self.doc_name)

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.close()

def load_profile(source_cache, include_doc):
    """
    Loads a Profile given an include document fragment, e.g.::

        profile: profile.yaml
        dir: /path/to/local/directory

    or::

        profile: linux/profile.yaml
        urls: [git://github.com/hashdist/hashstack.git]
        key: git:5aeba2c06ed1458ae4dc5d2c56bcf7092827347e

    The load happens recursively, including fetching any remote
    dependencies.
    """
    profile_rel_file = include_doc['profile']
    if 'dir' in include_doc:
        basedir = include_doc['dir']
        created_basedir = False
    else:
        # check out git repo to temporary directory
        assert len(include_doc['urls']) == 1
        basedir = tempfile.mkdtemp()
        created_basedir = True
        source_cache.fetch(include_doc['urls'][0], include_doc['key'], 'stack-desc')
        source_cache.unpack(include_doc['key'], basedir)


    assert os.path.isabs(basedir)
    with open(pjoin(basedir, profile_rel_file)) as f:
        doc = marked_yaml_load(f)
    if 'extends' in doc:
        extends = [load_profile(source_cache, parent_include) for parent_include in doc['extends']]
        del doc['extends']
    else:
        extends = []
    return Profile(basedir, profile_rel_file, doc, extends, rm_on_close=created_basedir)
