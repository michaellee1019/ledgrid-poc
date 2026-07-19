#!/usr/bin/env python3
"""
Animation plugin loader and manager
"""

import sys
import importlib.util
import inspect
import traceback
from typing import Dict, List, Type, Optional, Any, Iterable
from pathlib import Path

from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP

from .base import AnimationBase


class AnimationPluginLoader:
    """Loads and manages animation plugins"""
    
    def __init__(self, plugins_dir: Optional[str] = None, allowed_plugins: Optional[Iterable[str]] = None):
        """
        Initialize plugin loader
        
        Args:
            plugins_dir: Directory containing animation plugins
            allowed_plugins: Optional iterable of plugin stems to load (others are ignored)
        """
        if plugins_dir is None:
            plugins_dir = str(Path(__file__).resolve().parents[1] / "plugins")
        self.plugins_dir = Path(plugins_dir)
        self.plugins_dir.mkdir(exist_ok=True)

        self.allowed_plugins = set(allowed_plugins) if allowed_plugins else None
        
        # Ensure repo root and plugins directory are in Python path
        repo_root = self.plugins_dir.parent.parent
        if (repo_root / "drivers").exists() and str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        if str(self.plugins_dir.absolute()) not in sys.path:
            sys.path.insert(0, str(self.plugins_dir.absolute()))
        
        self.loaded_plugins: Dict[str, Type[AnimationBase]] = {}
        self.plugin_files: Dict[str, Path] = {}
        
    def scan_plugins(self) -> List[str]:
        """
        Scan plugins directory for animation files
        
        Returns:
            List of plugin names found
        """
        plugin_names = []
        self.plugin_files.clear()
        
        for file_path in self.plugins_dir.glob("*.py"):
            if file_path.name.startswith("__"):
                continue
                
            plugin_name = file_path.stem
            if self.allowed_plugins and plugin_name not in self.allowed_plugins:
                continue
            plugin_names.append(plugin_name)
            self.plugin_files[plugin_name] = file_path
            
        return plugin_names
    
    def load_plugin(self, plugin_name: str) -> Optional[Type[AnimationBase]]:
        """
        Load a single animation plugin
        
        Args:
            plugin_name: Name of the plugin to load
            
        Returns:
            Animation class if successful, None if failed
        """
        try:
            file_path = self.plugin_files.get(plugin_name)
            if not file_path or not file_path.exists():
                print(f"Plugin file not found: {plugin_name}")
                return None
            
            # Load module from file
            spec = importlib.util.spec_from_file_location(plugin_name, file_path)
            if spec is None or spec.loader is None:
                print(f"Could not create spec for plugin: {plugin_name}")
                return None
                
            module = importlib.util.module_from_spec(spec)

            # Standard imports register a module before executing it. Mirror that
            # behavior so decorators (notably dataclasses) and runtime type
            # resolution can look the module up while its body is executing.
            previous_module = sys.modules.get(plugin_name)
            sys.modules[plugin_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                if previous_module is None:
                    sys.modules.pop(plugin_name, None)
                else:
                    sys.modules[plugin_name] = previous_module
                raise
            # Find animation class defined in this module (skip imported bases).
            # Without the __module__ / isabstract checks, plugins that import
            # StatefulAnimationBase would bind that abstract class instead.
            animation_class = None
            for name, obj in inspect.getmembers(module):
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, AnimationBase)
                    and obj is not AnimationBase
                    and obj.__module__ == module.__name__
                    and not inspect.isabstract(obj)
                ):
                    animation_class = obj
                    break

            if animation_class is None:
                print(f"No animation class found in plugin: {plugin_name}")
                return None
            
            self.loaded_plugins[plugin_name] = animation_class
            print(f"✓ Loaded plugin: {plugin_name} -> {animation_class.__name__}")
            return animation_class
            
        except Exception as e:
            print(f"✗ Failed to load plugin {plugin_name}: {e}")
            traceback.print_exc()
            return None
    
    def load_all_plugins(self) -> Dict[str, Type[AnimationBase]]:
        """
        Load all plugins from the plugins directory
        
        Returns:
            Dict mapping plugin names to animation classes
        """
        plugin_names = self.scan_plugins()
        self.loaded_plugins.clear()
        
        for plugin_name in plugin_names:
            self.load_plugin(plugin_name)
        
        return self.loaded_plugins.copy()
    
    def reload_plugin(self, plugin_name: str) -> Optional[Type[AnimationBase]]:
        """
        Reload a specific plugin (hot reload)
        
        Args:
            plugin_name: Name of plugin to reload
            
        Returns:
            Reloaded animation class if successful
        """
        print(f"🔄 Reloading plugin: {plugin_name}")
        return self.load_plugin(plugin_name)
    
    def get_plugin(self, plugin_name: str) -> Optional[Type[AnimationBase]]:
        """Get a loaded plugin by name"""
        return self.loaded_plugins.get(plugin_name)
    
    def get_plugin_file(self, plugin_name: str) -> Optional[Path]:
        """Get the backing file path for a loaded plugin"""
        return self.plugin_files.get(plugin_name)
    
    def list_plugins(self) -> List[str]:
        """Get list of loaded plugin names"""
        return list(self.loaded_plugins.keys())
    
    def get_plugin_info(self, plugin_name: str) -> Optional[Dict[str, Any]]:
        """Get metadata about a plugin"""
        plugin_class = self.get_plugin(plugin_name)
        if plugin_class is None:
            return None

        # Build a lightweight controller so plugins that inspect dimensions don't crash
        class _InfoController:
            strip_count = DEFAULT_STRIP_COUNT
            leds_per_strip = DEFAULT_LEDS_PER_STRIP
            total_leds = strip_count * leds_per_strip
            debug = False

        try:
            temp_instance = plugin_class(_InfoController())
            info = temp_instance.get_info()
            info['plugin_name'] = plugin_name
            info['file_path'] = str(self.plugin_files.get(plugin_name, ''))
            return info
        except Exception as e:
            return {
                'plugin_name': plugin_name,
                'name': plugin_class.__name__,
                'error': str(e),
                'file_path': str(self.plugin_files.get(plugin_name, ''))
            }
