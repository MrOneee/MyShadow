"""Only locally installed, explicitly enabled code is importable."""
import importlib.util
import json
import re
import sys
from pathlib import Path


class Registry:
    def __init__(self,root,enabled):
        self.packages={}
        root=Path(root).resolve()
        for name in enabled:
            if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',name):raise ValueError('Invalid skill name')
            directory=(root/name).resolve()
            if not directory.is_relative_to(root):raise ValueError('Skill path escaped root')
            manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
            if manifest.get('id')!=name or manifest.get('api_version')!=1:raise ValueError('Unsupported skill manifest')
            if not isinstance(manifest.get('state_version'),int) or manifest['state_version']<1:raise ValueError('Invalid state version')
            entry=(directory/manifest['entry']).resolve()
            if not entry.is_relative_to(directory) or entry.suffix!='.py':raise ValueError('Invalid skill entry')
            module_name='installed_skills.'+name.replace('-','_')
            spec=importlib.util.spec_from_file_location(module_name,entry,submodule_search_locations=[str(directory)])
            module=importlib.util.module_from_spec(spec);sys.modules[module_name]=module;spec.loader.exec_module(module)
            instance=module.Skill(directory,manifest)
            instance.instructions=(directory/'SKILL.md').read_text(encoding='utf-8')
            self.packages[name]=instance

    def match(self,text):
        matches=[name for name,skill in self.packages.items() if skill.matches(text)]
        return matches[0] if len(matches)==1 else None

    def get(self,name):
        if name not in self.packages:raise RuntimeError('Activity skill is not enabled')
        return self.packages[name]

    def describe(self):
        return [{'id':name,'description':s.manifest['description']} for name,s in self.packages.items()]
