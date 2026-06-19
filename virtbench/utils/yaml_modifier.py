"""
YAML modification utilities for storage class injection
"""

import atexit
from pathlib import Path
from typing import Union

import yaml


class YAMLModifier:
    """Context manager for temporary YAML modifications"""

    def __init__(self, template_path: Union[str, Path], storage_class: str):
        self.template_path = Path(template_path)
        self.storage_class = storage_class
        self.original_content = None

    def __enter__(self):
        """Modify the YAML file"""
        # Read original content
        self.original_content = self.template_path.read_text()

        # Modify content
        modified_content = self._modify_content(self.original_content)

        # Write modified content
        self.template_path.write_text(modified_content)

        return self.template_path

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Restore original content"""
        if self.original_content:
            self.template_path.write_text(self.original_content)

    def _modify_content(self, content: str) -> str:
        """
        Modify YAML content to inject storage class.

        Args:
            content: Original YAML content

        Returns:
            Modified YAML content
        """
        # Simple string replacement for {{STORAGE_CLASS_NAME}} placeholder
        if "{{STORAGE_CLASS_NAME}}" in content:
            return content.replace("{{STORAGE_CLASS_NAME}}", self.storage_class)

        # Parse YAML and modify storageClassName field
        data = yaml.safe_load(content)

        # Navigate to dataVolumeTemplates and update storageClassName
        modified = False
        if "spec" in data and "dataVolumeTemplates" in data["spec"]:
            for dv_template in data["spec"]["dataVolumeTemplates"]:
                if "spec" in dv_template and "storage" in dv_template["spec"]:
                    dv_template["spec"]["storage"]["storageClassName"] = self.storage_class
                    modified = True

        if not modified:
            raise ValueError(
                f"Could not find storageClassName field or {{{{STORAGE_CLASS_NAME}}}} "
                f"placeholder in template: {self.template_path}"
            )

        return yaml.dump(data, default_flow_style=False, sort_keys=False)


def modify_storage_class(template_path: Union[str, Path], storage_class: str) -> None:
    """
    Modify storage class in VM template YAML file in-place.
    Automatically restores original content on program exit.

    Args:
        template_path: Path to VM template YAML file
        storage_class: Storage class name to inject
    """
    template_path = Path(template_path)

    # Read original content
    original_content = template_path.read_text()

    # Simple string replacement for {{STORAGE_CLASS_NAME}} placeholder
    if "{{STORAGE_CLASS_NAME}}" in original_content:
        modified_content = original_content.replace("{{STORAGE_CLASS_NAME}}", storage_class)
    else:
        # Parse YAML and modify storageClassName field
        data = yaml.safe_load(original_content)

        # Navigate to dataVolumeTemplates and update storageClassName
        modified = False
        if "spec" in data and "dataVolumeTemplates" in data["spec"]:
            for dv_template in data["spec"]["dataVolumeTemplates"]:
                if "spec" in dv_template and "storage" in dv_template["spec"]:
                    dv_template["spec"]["storage"]["storageClassName"] = storage_class
                    modified = True

        if not modified:
            raise ValueError(
                f"Could not find storageClassName field or {{{{STORAGE_CLASS_NAME}}}} "
                f"placeholder in template: {template_path}"
            )

        modified_content = yaml.dump(data, default_flow_style=False, sort_keys=False)

    # Write modified content
    template_path.write_text(modified_content)

    # Register cleanup to restore original content on exit
    atexit.register(lambda: template_path.write_text(original_content))
