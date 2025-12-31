# LED Grid Control System - Refactor Design and Plan

**Last Updated:** 2025-12-25  
**Status:** Planning Complete - Awaiting Approval  
**Estimated Duration:** 4 weeks  
**Repository:** ledgrid-poc  
**Single Source of Truth:** This file consolidates refactor* documentation.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Executive Summary](#executive-summary)
3. [Current State Analysis](#current-state-analysis)
4. [Proposed Architecture](#proposed-architecture)
5. [Refactoring Roadmap](#refactoring-roadmap)
6. [Execution Checklist](#execution-checklist)
7. [Build System Design](#build-system-design)
8. [Component Contracts & Interfaces](#component-contracts--interfaces)
9. [Development Workflow](#development-workflow)
10. [TODO Workflow & Registry](#todo-workflow--registry)
11. [Session Notes](#session-notes)
12. [Open Questions & Assumptions](#open-questions--assumptions)
13. [Decisions Log](#decisions-log)
14. [Next Steps](#next-steps)

---

## Quick Start

**Current Status:** Planning complete - awaiting approval  
**Next Action:** Review this plan and approve Phase 1 work  
**Command:** Use `/refactor` to start or resume (see `.codex/commands/refactor.md`)

### What We're Doing

- Reorganizing into a layered architecture with clear interfaces.
- Removing dead code while preserving working functionality.
- Adding observability and diagnostics to locate bottlenecks.
- Standardizing build and deployment workflows.
- Consolidating docs into this single plan.

### What We're Not Doing

- Rewriting working core logic.
- Changing user-facing behavior during Phase 1-2.
- Changing firmware protocol unless required in later phases.
- Adding new features outside the refactor scope.

### Key Decisions

- Keep file-based IPC for now to preserve process isolation.
- Adopt the layered structure: firmware -> drivers -> animation -> web -> ipc.
- Add metrics at each layer for debugging.
- Use Justfile for build automation.

### Critical Paths (Do Not Break)

- Deployment workflow.
- Web interface.
- Animation system.
- SPI communication.

### Key Files to Understand

- `start_animation_server.py` (entry point)
- `animation_manager.py` (animation coordination)
- `led_controller_spi_multi.py` (multi-device control)
- `web_interface.py` (web UI)
- `esp32_led_controller/src/main.cpp` (firmware)

### Top Priority Open Questions

- Keep or remove `water_simulation*.py`?
- Which animations are actively used in production?
- Purpose of `extract_frame_payload.py`?
- Target deployment environment details?
- Acceptable UI to LED latency?

### TODO Intake (Short Form)

Add TODOs in "TODO Workflow & Registry" using the template. Each TODO must include
Phase, Priority, and Acceptance criteria. The `/refactor` command triages TODOs and
aligns them with the plan.

## Executive Summary

### Project Overview

The LED Grid Control System is a multi-layered hardware control system that has evolved from a proof-of-concept into a production-ready animation framework. The system controls 2,240 LEDs (16 strips × 140 LEDs) across 2 ESP32 devices via SPI communication.

**Current Capabilities:**
- Real-time LED animation with 40+ FPS performance
- Web-based control interface with live preview
- Hot-swappable animation plugin system
- Multi-device hardware coordination
- Hardware diagnostics and debugging tools

**Key Pain Points:**
1. Flat directory structure makes navigation difficult
2. Significant dead code from POC iterations
3. No clear abstraction boundaries between layers
4. Difficult to trace data flow from UI → Animation → Hardware
5. Hardware debugging workflow is cumbersome
6. Inconsistent build/deployment tooling

### Goals

1. **Establish clear architectural layers** with well-defined interfaces
2. **Remove dead code** while preserving working functionality
3. **Simplify debugging workflow** for hardware throughput issues
4. **Enable clean add-on development** through proper abstraction
5. **Document the system** for future development and AI assistance

---

## Current State Analysis

### Repository Statistics

- **Python files:** 77 (excluding dependencies)
- **C++ files:** 2 (main.cpp + backup)
- **Header files:** 31 (mostly FastLED library)
- **Total LOC:** ~15,000+ (estimated)

### Directory Structure (Current)

```
ledgrid-poc/
├── animation_system/          # Core animation framework (GOOD)
│   ├── animation_base.py      # Base classes for animations
│   └── plugin_loader.py       # Plugin discovery and loading
├── animations/                # Animation plugins (GOOD)
│   ├── rainbow.py, sparkle.py, emoji.py, etc.
│   └── [17 animation files]
├── debugging/                 # Mixed debugging tools (NEEDS CLEANUP)
│   └── [50+ test scripts, many obsolete]
├── esp32_led_controller/      # Hardware firmware (GOOD)
│   ├── platformio.ini
│   └── src/main.cpp
├── templates/                 # Web UI templates (GOOD)
├── scripts/                   # Deployment helpers (GOOD)
├── Root level files          # NEEDS ORGANIZATION
│   ├── animation_manager.py   # Core service
│   ├── web_interface.py       # Flask app
│   ├── led_controller_spi.py  # SPI driver
│   ├── led_controller_spi_multi.py  # Multi-device driver
│   ├── control_channel.py     # IPC mechanism
│   ├── frame_data_codec.py    # Data compression
│   └── [20+ other files]
└── Documentation files        # Multiple README files (CONSOLIDATE)
```

### Component Inventory

#### ✅ Production Components (Keep & Organize)

**Hardware Layer:**
- `esp32_led_controller/src/main.cpp` - ESP32 firmware with SPI slave + FastLED
- `esp32_led_controller/platformio.ini` - PlatformIO configuration

**Driver Layer:**
- `led_controller_spi.py` - Single-device SPI controller
- `led_controller_spi_multi.py` - Multi-device coordinator with parallel writes
- `frame_data_codec.py` - Frame data compression (zlib + base64)
- `led_layout.py` - Central LED configuration (16 strips × 140 LEDs)

**Animation Framework:**
- `animation_system/animation_base.py` - Base classes (AnimationBase, StatefulAnimationBase)
- `animation_system/plugin_loader.py` - Hot-reload plugin system
- `animation_manager.py` - Animation coordination service

**Animation Plugins (Active):**
- `animations/rainbow.py` - Classic rainbow effects
- `animations/sparkle.py` - Sparkle effects
- `animations/emoji.py` - Emoji display system
- `animations/emoji_arranger.py` - Interactive emoji placement
- `animations/fluid_tank.py` - Fluid simulation
- `animations/flame_burst.py` - Fire effects
- `animations/christmas_tree.py` - Holiday animation
- `animations/tetris.py` - Tetris game
- `animations/simple_test.py` - Hardware test patterns
- `animations/hardware_diagnostics.py` - Hardware debugging

**Web Layer:**
- `web_interface.py` - Flask application with REST API
- `templates/` - HTML templates (base, index, control, upload, emoji_arranger)
- `control_channel.py` - File-based IPC between web and controller processes

**Entry Points:**
- `start_animation_server.py` - Main startup script (controller or web mode)

**Deployment:**
- `deploy.sh` - Deployment script to Raspberry Pi
- `Justfile` - Build automation (minimal)
- `requirements.txt` - Python dependencies

#### ⚠️ Questionable Components (Review Needed)

**Root Level Files:**
- `water_simulation.py` - Standalone water sim (duplicate of fluid_tank?)
- `water_simulation_server.py` - Server version (unused?)
- `debug_emoji.py` - Emoji debugging (obsolete?)
- `demo_animation_system.py` - Demo script (keep for testing?)
- `test_animation_system.py` - Test script (keep?)
- `test_plugins_only.py` - Plugin test (keep?)
- `test_venv_deploy.py` - Deployment test (keep?)
- `extract_frame_payload.py` - Utility script (purpose?)

**Animation Duplicates:**
- `animations/led_controller_spi.py` - Duplicate of root file?
- `animations/led_controller_spi_standalone.py` - Standalone version?
- `animations/test_animation.py` - Test animation (keep?)
- `animations/debug_sequential.py` - Debug animation (keep?)
- `animations/effects.py` - Generic effects (active?)
- `animations/solid.py` - Solid colors (active?)
- `animations/ascii_drop.py` - ASCII animation (active?)

#### ❌ Dead Code (Remove)

**Debugging Directory (50+ files, mostly obsolete):**
- `debugging/test_*.py` - 30+ test scripts from hardware bring-up
- `debugging/diagnose_*.py` - Diagnostic scripts (consolidate?)
- `debugging/led_controller.py` - Old controller implementation
- `debugging/led_controller_spi_bitbang.py` - Bitbang SPI (obsolete)
- `debugging/fluid_tank_simulation.py` - Duplicate?
- `debugging/*.sh` - Shell scripts for pin testing
- `debugging/*.ino` - Arduino test sketches

**Backup Files:**
- `esp32_led_controller/src/main_spi.cpp.bak` - Backup file (remove)

**Documentation Overlap:**
- Multiple README files with overlapping content (consolidate)

### Data Flow Analysis

#### Current Flow: UI → Hardware

```
User Browser
    ↓ HTTP POST
web_interface.py (Flask)
    ↓ write JSON
control_channel.py (FileControlChannel)
    ↓ write run_state/control.json
[File System]
    ↓ poll & read
start_animation_server.py (controller mode)
    ↓ parse command
animation_manager.py (AnimationManager)
    ↓ load plugin
animation_system/plugin_loader.py
    ↓ instantiate
animations/*.py (AnimationBase subclass)
    ↓ generate_frame()
animation_manager.py
    ↓ set_all_pixels()
led_controller_spi_multi.py (MultiDeviceLEDController)
    ↓ split frame by device
led_controller_spi.py (LEDController) × 2
    ↓ SPI transfer
/dev/spidev0.0, /dev/spidev0.1
    ↓ SPI slave
ESP32 main.cpp (SPI interrupt handler)
    ↓ parse command
FastLED library
    ↓ parallel output
8 LED strips × 140 LEDs (per device)
```

**Pain Points in Current Flow:**
1. File-based IPC adds latency but enables process separation
2. No visibility into SPI throughput or frame timing
3. Hardware diagnostics require firmware reflash
4. Difficult to trace which component is bottleneck

---

## Proposed Architecture

### Design Principles

1. **Layered Architecture** - Clear separation between hardware, driver, framework, and UI
2. **Interface-Driven** - Each layer exposes well-defined contracts
3. **Observability** - Built-in metrics and debugging at each layer
4. **Modularity** - Components can be tested and developed independently
5. **Documentation** - Self-documenting code with clear contracts

### New Directory Structure

```
ledgrid-poc/
├── docs/                          # 📚 All documentation
│   ├── README.md                  # Main project documentation
│   ├── ARCHITECTURE.md            # System architecture
│   ├── DEPLOYMENT.md              # Deployment guide
│   ├── DEVELOPMENT.md             # Developer guide
│   ├── API.md                     # REST API reference
│   └── refactor.md                # This file (living document)
│
├── firmware/                      # 🔧 Hardware firmware layer
│   ├── esp32/                     # ESP32-specific firmware
│   │   ├── platformio.ini
│   │   ├── src/main.cpp
│   │   └── README.md
│   └── Justfile                   # Firmware build tasks
│
├── drivers/                       # 🔌 Hardware communication layer
│   ├── __init__.py
│   ├── spi_controller.py          # Single-device SPI driver
│   ├── multi_device.py            # Multi-device coordinator
│   ├── frame_codec.py             # Frame data encoding/decoding
│   ├── led_layout.py              # LED configuration
│   └── README.md
│
├── animation/                     # 🎨 Animation framework layer
│   ├── core/                      # Core framework
│   │   ├── __init__.py
│   │   ├── base.py                # AnimationBase classes
│   │   ├── manager.py             # AnimationManager
│   │   ├── plugin_loader.py       # Plugin system
│   │   └── README.md
│   ├── plugins/                   # Animation plugins
│   │   ├── __init__.py
│   │   ├── rainbow.py
│   │   ├── sparkle.py
│   │   ├── emoji.py
│   │   ├── fluid_tank.py
│   │   └── [other animations]
│   └── Justfile                   # Animation development tasks
│
├── web/                           # 🌐 Web interface layer
│   ├── __init__.py
│   ├── app.py                     # Flask application
│   ├── api.py                     # REST API routes
│   ├── templates/                 # HTML templates
│   │   ├── base.html
│   │   ├── index.html
│   │   ├── control.html
│   │   └── upload.html
│   ├── static/                    # Static assets (if needed)
│   └── README.md
│
├── ipc/                           # 🔄 Inter-process communication
│   ├── __init__.py
│   ├── control_channel.py         # File-based IPC
│   └── README.md
│
├── tools/                         # 🛠️ Development & debugging tools
│   ├── diagnostics/               # Hardware diagnostics
│   │   ├── hardware_test.py       # Comprehensive hardware test
│   │   ├── spi_analyzer.py        # SPI throughput analysis
│   │   └── strip_test.py          # Individual strip testing
│   ├── deployment/                # Deployment utilities
│   │   ├── deploy.py              # Main deployment script
│   │   └── manage_venv.sh         # Virtual environment management
│   └── dev/                       # Development utilities
│       ├── demo.py                # System demo
│       └── benchmark.py           # Performance benchmarking
│
├── tests/                         # 🧪 Test suite
│   ├── unit/                      # Unit tests
│   ├── integration/               # Integration tests
│   └── fixtures/                  # Test fixtures
│
├── config/                        # ⚙️ Configuration files
│   ├── default.json               # Default configuration
│   ├── production.json            # Production settings
│   └── development.json           # Development settings
│
├── scripts/                       # 📜 Utility scripts
│   ├── start_controller.py        # Start controller process
│   ├── start_web.py               # Start web process
│   └── start_all.py               # Start both processes
│
├── run_state/                     # 🏃 Runtime state (gitignored)
│   ├── control.json               # Control commands
│   ├── status.json                # System status
│   └── *.pid                      # Process IDs
│
├── Justfile                       # 🔨 Top-level build automation
├── requirements.txt               # Python dependencies
├── .gitignore
└── README.md                      # Quick start guide
```

### Layer Responsibilities

#### 1. Firmware Layer (`firmware/`)

**Responsibility:** Low-level hardware control and LED driving

**Key Components:**
- ESP32 SPI slave implementation
- FastLED integration for parallel LED output
- Command protocol parsing
- Hardware diagnostics support

**Interface:**
- SPI command protocol (documented in firmware/esp32/README.md)
- Status reporting via SPI responses

#### 2. Driver Layer (`drivers/`)

**Responsibility:** Hardware abstraction and communication

**Key Components:**
- `spi_controller.py` - Single ESP32 device control
- `multi_device.py` - Multi-device coordination
- `frame_codec.py` - Frame data compression
- `led_layout.py` - LED configuration constants

**Interface:**
```python
class LEDController:
    def set_pixel(self, pixel: int, r: int, g: int, b: int) -> None
    def set_all_pixels(self, colors: List[Tuple[int, int, int]]) -> None
    def set_brightness(self, brightness: int) -> None
    def show(self) -> None
    def clear(self) -> None
    def get_stats(self) -> Dict[str, Any]  # NEW: Performance metrics
```

#### 3. Animation Framework Layer (`animation/`)

**Responsibility:** Animation plugin system and frame generation

**Key Components:**
- `core/base.py` - AnimationBase and StatefulAnimationBase classes
- `core/manager.py` - Animation lifecycle management
- `core/plugin_loader.py` - Hot-reload plugin system
- `plugins/` - Individual animation implementations

**Interface:**
```python
class AnimationBase(ABC):
    @abstractmethod
    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]
    def update_parameters(self, new_params: Dict[str, Any]) -> None
    def get_runtime_stats(self) -> Dict[str, Any]  # NEW: Animation metrics
    def start(self) -> None
    def stop(self) -> None

class AnimationManager:
    def start_animation(self, name: str, config: Dict[str, Any]) -> bool
    def stop_animation(self) -> None
    def update_parameters(self, params: Dict[str, Any]) -> None
    def get_status(self) -> Dict[str, Any]
    def get_performance_metrics(self) -> Dict[str, Any]  # NEW: Performance data
```

#### 4. Web Layer (`web/`)

**Responsibility:** User interface and REST API

**Key Components:**
- `app.py` - Flask application setup
- `api.py` - REST API endpoints
- `templates/` - HTML templates

**Interface (REST API):**
```
GET  /api/status              - System status
GET  /api/animations          - List available animations
POST /api/animation/start     - Start animation
POST /api/animation/stop      - Stop animation
POST /api/animation/params    - Update parameters
GET  /api/metrics             - Performance metrics (NEW)
GET  /api/hardware/stats      - Hardware statistics (NEW)
```

#### 5. IPC Layer (`ipc/`)

**Responsibility:** Communication between web and controller processes

**Key Components:**
- `control_channel.py` - File-based command/status exchange

**Interface:**
```python
class ControlChannel:
    def send_command(self, action: str, **data) -> Dict[str, Any]
    def read_status(self) -> Optional[Dict[str, Any]]
    def write_status(self, payload: Dict[str, Any]) -> None
```

### Observability & Debugging

#### New Metrics Collection

Each layer will expose metrics for debugging:

**Driver Layer Metrics:**
- SPI transfer rate (bytes/sec)
- Frame send duration (ms)
- Command queue depth
- Error count

**Animation Layer Metrics:**
- Frame generation time (ms)
- Target vs actual FPS
- Parameter update latency
- Plugin load time

**System Metrics:**
- End-to-end latency (UI click → LED update)
- Memory usage
- CPU usage per process

#### Hardware Diagnostics

Consolidated into `tools/diagnostics/`:
- `hardware_test.py` - Comprehensive test suite
- `spi_analyzer.py` - Real-time SPI throughput analysis
- `strip_test.py` - Individual strip testing

---

## Refactoring Roadmap

### Phase 1: Foundation (Week 1)

**Goal:** Establish new structure without breaking existing functionality

**Tasks:**
1. ✅ Create new directory structure
2. ✅ Move files to new locations (with git mv to preserve history)
3. ✅ Update import paths throughout codebase
4. ✅ Create README.md in each directory
5. ✅ Verify system still works end-to-end
6. ✅ Update deployment scripts

**Success Criteria:**
- All existing functionality works
- No broken imports
- Deployment script works
- Tests pass (if any exist)

### Phase 2: Dead Code Removal (Week 1-2)

**Goal:** Remove obsolete code and consolidate duplicates

**Tasks:**
1. ✅ Archive debugging/ directory (move to archive/ or delete)
2. ✅ Remove backup files (*.bak)
3. ✅ Consolidate duplicate implementations
4. ✅ Remove unused animations
5. ✅ Clean up root directory
6. ✅ Update .gitignore

**Success Criteria:**
- Repository size reduced by ~30%
- No duplicate functionality
- Clear which code is active

### Phase 3: Interface Standardization (Week 2)

**Goal:** Establish clear contracts between layers

**Tasks:**
1. ✅ Add get_stats() to LEDController
2. ✅ Add get_runtime_stats() to AnimationBase
3. ✅ Add metrics endpoints to web API
4. ✅ Document all interfaces in README files
5. ✅ Add type hints throughout
6. ✅ Create interface documentation

**Success Criteria:**
- All layers have documented interfaces
- Type hints on all public methods
- Interface documentation complete

### Phase 4: Observability (Week 3)

**Goal:** Add metrics and debugging capabilities

**Tasks:**
1. ✅ Implement metrics collection in drivers
2. ✅ Add performance tracking to AnimationManager
3. ✅ Create metrics API endpoints
4. ✅ Build SPI analyzer tool
5. ✅ Add hardware diagnostics dashboard
6. ✅ Create debugging guide

**Success Criteria:**
- Can measure SPI throughput in real-time
- Can identify bottlenecks in animation pipeline
- Hardware issues are easy to diagnose

### Phase 5: Build System (Week 3-4)

**Goal:** Unified build and deployment automation

**Tasks:**
1. ✅ Create top-level Justfile
2. ✅ Create layer-specific Justfiles
3. ✅ Standardize common tasks (test, lint, deploy)
4. ✅ Document all build commands
5. ✅ Create development setup script

**Success Criteria:**
- Single command to deploy
- Single command to run tests
- Single command to start development environment

### Phase 6: Documentation (Week 4)

**Goal:** Comprehensive documentation for all components

**Tasks:**
1. ✅ Consolidate README files
2. ✅ Create architecture documentation
3. ✅ Document all APIs
4. ✅ Create developer guide
5. ✅ Create troubleshooting guide
6. ✅ Update this refactor.md with lessons learned

**Success Criteria:**
- New developer can get started in <30 minutes
- All interfaces are documented
- Common issues have solutions documented

---

## Execution Checklist

This section merges the prior `REFACTOR_CHECKLIST.md` into this plan. Track progress by
checking items as they complete.

### Pre-Refactoring

- [ ] **User approval received** for refactoring plan
- [ ] **Backup created** of current working state
- [ ] **Git branch created** for refactoring work
- [ ] **Critical paths identified** and documented
- [ ] **Test plan created** for verification
- [ ] **Rollback plan documented** in case of issues

---

### Phase 1: Foundation (Week 1)

**Goal:** Establish new structure without breaking functionality

#### Directory Creation
- [ ] Create `docs/` directory
- [ ] Create `firmware/esp32/` directory
- [ ] Create `drivers/` directory
- [ ] Create `animation/core/` directory
- [ ] Create `animation/plugins/` directory
- [ ] Create `web/` directory
- [ ] Create `ipc/` directory
- [ ] Create `tools/diagnostics/` directory
- [ ] Create `tools/deployment/` directory
- [ ] Create `tools/dev/` directory
- [ ] Create `tests/unit/` directory
- [ ] Create `tests/integration/` directory
- [ ] Create `config/` directory
- [ ] Create `scripts/` directory

#### File Migration (Use git mv!)
- [ ] Move `esp32_led_controller/*` -> `firmware/esp32/`
- [ ] Move `led_controller_spi.py` -> `drivers/spi_controller.py`
- [ ] Move `led_controller_spi_multi.py` -> `drivers/multi_device.py`
- [ ] Move `frame_data_codec.py` -> `drivers/frame_codec.py`
- [ ] Move `led_layout.py` -> `drivers/led_layout.py`
- [ ] Move `animation_system/animation_base.py` -> `animation/core/base.py`
- [ ] Move `animation_system/plugin_loader.py` -> `animation/core/plugin_loader.py`
- [ ] Move `animation_manager.py` -> `animation/core/manager.py`
- [ ] Move `animations/*.py` -> `animation/plugins/`
- [ ] Move `web_interface.py` -> `web/app.py`
- [ ] Move `templates/` -> `web/templates/`
- [ ] Move `control_channel.py` -> `ipc/control_channel.py`
- [ ] Move `deploy.sh` -> `tools/deployment/deploy.sh`
- [ ] Move `start_animation_server.py` -> `scripts/start_server.py`

#### Import Path Updates
- [ ] Update imports in `drivers/` files
- [ ] Update imports in `animation/core/` files
- [ ] Update imports in `animation/plugins/` files
- [ ] Update imports in `web/` files
- [ ] Update imports in `ipc/` files
- [ ] Update imports in `scripts/` files
- [ ] Update imports in `tools/` files

#### Documentation
- [ ] Create `drivers/README.md`
- [ ] Create `animation/core/README.md`
- [ ] Create `animation/plugins/README.md`
- [ ] Create `web/README.md`
- [ ] Create `ipc/README.md`
- [ ] Create `firmware/esp32/README.md`
- [ ] Create `tools/README.md`
- [ ] Move existing docs to `docs/`

#### Verification
- [ ] All imports resolve correctly
- [ ] Web interface starts without errors
- [ ] Controller process starts without errors
- [ ] Can start an animation via web UI
- [ ] LEDs respond to animation
- [ ] Deployment script works
- [ ] No broken references in code

#### Git
- [ ] Commit Phase 1 changes
- [ ] Tag as `refactor-phase-1`
- [ ] Push to remote

---

### Phase 2: Dead Code Removal (Week 1-2)

**Goal:** Remove obsolete code and consolidate duplicates

#### Dead Code Removal
- [ ] Review `debugging/` directory with user
- [ ] Archive or delete `debugging/` directory
- [ ] Remove `esp32_led_controller/src/main_spi.cpp.bak`
- [ ] Remove any other `*.bak` files
- [ ] Remove `__pycache__/` directories (add to .gitignore)

#### Duplicate Consolidation
- [ ] Decide on `water_simulation.py` vs `fluid_tank.py`
- [ ] Remove duplicate if confirmed
- [ ] Decide on `water_simulation_server.py` fate
- [ ] Review `animations/led_controller_spi*.py` duplicates
- [ ] Remove confirmed duplicates
- [ ] Review `debug_emoji.py` - keep or remove?
- [ ] Review `extract_frame_payload.py` - keep or remove?

#### Test File Review
- [ ] Review `demo_animation_system.py` - move to tools/dev/?
- [ ] Review `test_animation_system.py` - move to tests/?
- [ ] Review `test_plugins_only.py` - move to tests/?
- [ ] Review `test_venv_deploy.py` - move to tools/deployment/?

#### Animation Cleanup
- [ ] Verify which animations are active
- [ ] Remove unused animations
- [ ] Consolidate `effects.py` and `solid.py` if needed
- [ ] Remove `test_animation.py` if not needed
- [ ] Remove `debug_sequential.py` if not needed

#### Documentation Consolidation
- [ ] Move `README.md` -> `docs/README.md`
- [ ] Move `README_ANIMATION_SYSTEM.md` -> `docs/ANIMATION_SYSTEM.md`
- [ ] Move `SYSTEM_COMPLETE.md` -> `docs/SYSTEM_COMPLETE.md`
- [ ] Move `DEPLOYMENT_GUIDE.md` -> `docs/DEPLOYMENT.md`
- [ ] Move `VENV_DEPLOYMENT_COMPLETE.md` -> archive or merge
- [ ] Move `WIRING.md` -> `docs/HARDWARE.md`
- [ ] Move `ASCII_DROP_ANIMATION.md` -> `docs/` or remove
- [ ] Create new root `README.md` with quick start

#### .gitignore Updates
- [ ] Add `__pycache__/`
- [ ] Add `*.pyc`
- [ ] Add `.venv/`
- [ ] Add `.venv-web/`
- [ ] Add `run_state/`
- [ ] Add `.pytest_cache/`
- [ ] Add `.mypy_cache/`
- [ ] Add `*.log`

#### Verification
- [ ] System still works end-to-end
- [ ] No broken imports
- [ ] Deployment still works
- [ ] Repository size reduced

#### Git
- [ ] Commit Phase 2 changes
- [ ] Tag as `refactor-phase-2`
- [ ] Push to remote

---

### Phase 3: Interface Standardization (Week 2)

**Goal:** Establish clear contracts between layers

#### Driver Layer Interfaces
- [ ] Add `get_stats()` to `LEDController`
- [ ] Add `get_stats()` to `MultiDeviceLEDController`
- [ ] Add type hints to all public methods
- [ ] Document interface in `drivers/README.md`
- [ ] Add docstrings to all public methods

#### Animation Layer Interfaces
- [ ] Add `get_runtime_stats()` to `AnimationBase`
- [ ] Add type hints to all public methods
- [ ] Document interface in `animation/core/README.md`
- [ ] Add docstrings to all public methods
- [ ] Update all plugins to match interface

#### Web Layer Interfaces
- [ ] Document REST API in `web/README.md`
- [ ] Add type hints to Flask routes
- [ ] Add docstrings to all endpoints
- [ ] Create `docs/API.md` with full API reference

#### IPC Layer Interfaces
- [ ] Document protocol in `ipc/README.md`
- [ ] Add type hints to `ControlChannel`
- [ ] Add docstrings to all methods
- [ ] Document JSON schemas

#### Verification
- [ ] All interfaces documented
- [ ] Type hints on all public methods
- [ ] Docstrings on all public methods
- [ ] Interface docs reviewed

#### Git
- [ ] Commit Phase 3 changes
- [ ] Tag as `refactor-phase-3`
- [ ] Push to remote

---

### Phase 4: Observability (Week 3)

**Goal:** Add metrics and debugging capabilities

#### Metrics Implementation
- [ ] Implement stats collection in `LEDController`
- [ ] Implement stats collection in `MultiDeviceLEDController`
- [ ] Implement stats collection in `AnimationManager`
- [ ] Add performance tracking to animation loop
- [ ] Add timing measurements for SPI transfers

#### API Endpoints
- [ ] Add `GET /api/metrics` endpoint
- [ ] Add `GET /api/hardware/stats` endpoint
- [ ] Add metrics to status.json
- [ ] Update web UI to display metrics

#### Diagnostic Tools
- [ ] Create `tools/diagnostics/hardware_test.py`
- [ ] Create `tools/diagnostics/spi_analyzer.py`
- [ ] Create `tools/diagnostics/strip_test.py`
- [ ] Document diagnostic tools in `tools/README.md`

#### Documentation
- [ ] Create debugging guide in `docs/DEBUGGING.md`
- [ ] Document metrics in `docs/METRICS.md`
- [ ] Add troubleshooting section to docs

#### Verification
- [ ] Can measure SPI throughput
- [ ] Can identify bottlenecks
- [ ] Diagnostic tools work
- [ ] Metrics API returns data

#### Git
- [ ] Commit Phase 4 changes
- [ ] Tag as `refactor-phase-4`
- [ ] Push to remote

---

### Phase 5: Build System (Week 3-4)

**Goal:** Unified build and deployment automation

#### Top-Level Justfile
- [ ] Create `Justfile` in root
- [ ] Add `setup` recipe
- [ ] Add `test` recipe
- [ ] Add `lint` recipe
- [ ] Add `format` recipe
- [ ] Add `start-controller` recipe
- [ ] Add `start-web` recipe
- [ ] Add `start-all` recipe
- [ ] Add `deploy` recipe
- [ ] Add `firmware-*` recipes
- [ ] Add `diagnose` recipe
- [ ] Add `clean` recipe
- [ ] Add `demo` recipe

#### Layer-Specific Justfiles
- [ ] Create `firmware/esp32/Justfile`
- [ ] Create `animation/Justfile`
- [ ] Add build/upload/monitor recipes to firmware
- [ ] Add test/new/validate recipes to animation

#### Script Updates
- [ ] Create `scripts/start_controller.py`
- [ ] Create `scripts/start_web.py`
- [ ] Create `scripts/start_all.py`
- [ ] Update deployment script to use new structure
- [ ] Create development setup script

#### Documentation
- [ ] Document all Justfile recipes
- [ ] Create `docs/BUILD.md`
- [ ] Add examples to README

#### Verification
- [ ] `just setup` works
- [ ] `just test` works (when tests exist)
- [ ] `just start-all` works
- [ ] `just deploy` works
- [ ] `just firmware-build` works

#### Git
- [ ] Commit Phase 5 changes
- [ ] Tag as `refactor-phase-5`
- [ ] Push to remote

---

### Phase 6: Documentation (Week 4)

**Goal:** Comprehensive documentation for all components

#### Architecture Documentation
- [ ] Create `docs/ARCHITECTURE.md`
- [ ] Document system layers
- [ ] Document data flow
- [ ] Document process architecture
- [ ] Add diagrams (use ARCHITECTURE_DIAGRAM.md)

#### API Documentation
- [ ] Create `docs/API.md`
- [ ] Document all REST endpoints
- [ ] Document request/response formats
- [ ] Add examples for each endpoint

#### Developer Guide
- [ ] Create `docs/DEVELOPMENT.md`
- [ ] Document development setup
- [ ] Document how to create animations
- [ ] Document how to debug issues
- [ ] Document testing procedures

#### Deployment Guide
- [ ] Update `docs/DEPLOYMENT.md`
- [ ] Document deployment process
- [ ] Document configuration options
- [ ] Document troubleshooting

#### Hardware Documentation
- [ ] Update `docs/HARDWARE.md`
- [ ] Document wiring
- [ ] Document firmware
- [ ] Document SPI protocol

#### Root README
- [ ] Create new root `README.md`
- [ ] Add quick start guide
- [ ] Add links to detailed docs
- [ ] Add system requirements
- [ ] Add screenshots/demos

#### Refactor Documentation
- [ ] Update `refactor.md` with lessons learned
- [ ] Document decisions made
- [ ] Document issues encountered
- [ ] Document solutions found
- [ ] Add recommendations for future work

#### Verification
- [ ] All docs reviewed for accuracy
- [ ] All links work
- [ ] Code examples tested
- [ ] New developer can get started in <30 min

#### Git
- [ ] Commit Phase 6 changes
- [ ] Tag as `refactor-phase-6`
- [ ] Tag as `refactor-complete`
- [ ] Push to remote

---

### Post-Refactoring

#### Final Verification
- [ ] Full end-to-end test
- [ ] Deploy to production
- [ ] Monitor for issues
- [ ] Verify performance metrics
- [ ] User acceptance testing

#### Cleanup
- [ ] Remove old branches
- [ ] Archive old documentation
- [ ] Clean up temporary files
- [ ] Update .gitignore if needed

#### Documentation
- [ ] Update refactor.md with final notes
- [ ] Document any remaining issues
- [ ] Create maintenance guide
- [ ] Update changelog

#### Handoff
- [ ] Review with user
- [ ] Transfer knowledge
- [ ] Document any quirks
- [ ] Provide support plan

---

### Notes & Issues

#### Issues Encountered

(Add issues as they come up during refactoring)

#### Solutions Applied

(Document solutions for future reference)

#### Lessons Learned

(Add lessons learned during refactoring)

#### Future Improvements

(Ideas for future enhancements)

**Checklist Version:** 1.0  
**Last Updated:** 2025-12-25  
**Status:** Ready for execution

---

## Build System Design

### Top-Level Justfile

```makefile
# ledgrid-poc/Justfile

set shell := ["bash", "-euxo", "pipefail", "-c"]

# Default recipe - show help
default:
    @just --list

# Development setup
setup:
    @echo "Setting up development environment..."
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
    @echo "✓ Development environment ready"
    @echo "  Activate with: source .venv/bin/activate"

# Run all tests
test:
    @echo "Running test suite..."
    .venv/bin/pytest tests/ -v

# Lint code
lint:
    @echo "Linting code..."
    .venv/bin/flake8 drivers/ animation/ web/ ipc/
    .venv/bin/mypy drivers/ animation/ web/ ipc/

# Format code
format:
    @echo "Formatting code..."
    .venv/bin/black drivers/ animation/ web/ ipc/ tools/

# Start controller process
start-controller:
    .venv/bin/python scripts/start_controller.py

# Start web interface
start-web:
    .venv/bin/python scripts/start_web.py

# Start both processes
start-all:
    .venv/bin/python scripts/start_all.py

# Deploy to Raspberry Pi
deploy:
    @echo "Deploying to Raspberry Pi..."
    .venv/bin/python tools/deployment/deploy.py

# Build firmware
firmware-build:
    cd firmware/esp32 && just build

# Upload firmware
firmware-upload:
    cd firmware/esp32 && just upload

# Monitor firmware serial output
firmware-monitor:
    cd firmware/esp32 && just monitor

# Run hardware diagnostics
diagnose:
    .venv/bin/python tools/diagnostics/hardware_test.py

# Analyze SPI performance
analyze-spi:
    .venv/bin/python tools/diagnostics/spi_analyzer.py

# Clean build artifacts
clean:
    rm -rf .venv __pycache__ **/__pycache__ *.pyc **/*.pyc
    rm -rf .pytest_cache .mypy_cache
    cd firmware/esp32 && just clean

# Development demo
demo:
    .venv/bin/python tools/dev/demo.py
```

### Firmware Justfile

```makefile
# firmware/esp32/Justfile

# Build firmware
build:
    pio run

# Upload firmware to device
upload:
    pio run --target upload

# Monitor serial output
monitor:
    pio device monitor

# Build and upload
flash: build upload

# Clean build artifacts
clean:
    pio run --target clean

# Update dependencies
update:
    pio pkg update
```

### Animation Development Justfile

```makefile
# animation/Justfile

# Test all animations
test:
    python -m pytest ../tests/unit/animation/ -v

# Create new animation from template
new name:
    @echo "Creating new animation: {{name}}"
    cp plugins/_template.py plugins/{{name}}.py
    @echo "✓ Created plugins/{{name}}.py"
    @echo "  Edit the file and implement generate_frame()"

# Validate animation plugin
validate name:
    python -c "from plugins.{{name}} import *; print('✓ Valid plugin')"

# List all animations
list:
    @ls -1 plugins/*.py | grep -v __pycache__ | grep -v _template
```

---

## Component Contracts & Interfaces

### Driver Layer Contract

**File:** `drivers/README.md`

#### LEDController Interface

```python
class LEDController:
    """
    Single ESP32 device controller via SPI.

    Attributes:
        strip_count: Number of LED strips (default: 8)
        leds_per_strip: LEDs per strip (default: 140)
        total_leds: Total LED count (strip_count × leds_per_strip)
        inline_show: If True, set_all_pixels() calls show() automatically
    """

    def __init__(self, bus: int = 0, device: int = 0, speed: int = 8_000_000,
                 mode: int = 3, strips: int = 8, leds_per_strip: int = 140,
                 debug: bool = False):
        """
        Initialize SPI LED controller.

        Args:
            bus: SPI bus number (0 = /dev/spidev0.X)
            device: SPI device number (0 = CE0, 1 = CE1)
            speed: SPI clock speed in Hz (default: 8 MHz)
            mode: SPI mode (default: 3 for CPOL=1, CPHA=1)
            strips: Number of LED strips
            leds_per_strip: LEDs per strip
            debug: Enable debug logging
        """

    def set_pixel(self, pixel: int, r: int, g: int, b: int) -> None:
        """Set single pixel color (0-indexed)."""

    def set_all_pixels(self, colors: List[Tuple[int, int, int]]) -> None:
        """
        Set all pixels at once (most efficient).

        Args:
            colors: List of (r, g, b) tuples, length must equal total_leds

        Note: Automatically calls show() if inline_show is True
        """

    def set_brightness(self, brightness: int) -> None:
        """Set global brightness (0-255)."""

    def show(self) -> None:
        """Update LED display with buffered data."""

    def clear(self) -> None:
        """Clear all LEDs to black."""

    def configure(self) -> None:
        """Send configuration to device (strips, leds_per_strip, debug mode)."""

    def get_stats(self) -> Dict[str, Any]:
        """
        Get performance statistics.

        Returns:
            {
                'spi_speed_hz': int,
                'total_leds': int,
                'last_frame_duration_ms': float,
                'avg_frame_duration_ms': float,
                'frames_sent': int,
                'bytes_sent': int,
                'errors': int
            }
        """
```

#### MultiDeviceLEDController Interface

```python
class MultiDeviceLEDController:
    """
    Coordinates multiple ESP32 devices for larger LED installations.

    Attributes:
        num_devices: Number of ESP32 devices
        strip_count: Total strips across all devices
        total_leds: Total LEDs across all devices
    """

    def __init__(self, num_devices: int = 2, bus: int = 0, speed: int = 8_000_000,
                 mode: int = 3, strips_per_device: int = 8, leds_per_strip: int = 140,
                 debug: bool = False, parallel: bool = True):
        """
        Initialize multi-device controller.

        Args:
            num_devices: Number of ESP32 devices (uses CE0, CE1, ...)
            parallel: If True, send data to devices in parallel threads
        """

    # Same interface as LEDController for compatibility
    def set_all_pixels(self, colors: List[Tuple[int, int, int]]) -> None:
        """Automatically splits frame across devices."""

    def get_stats(self) -> Dict[str, Any]:
        """Returns aggregated stats from all devices."""
```

### Animation Framework Contract

**File:** `animation/core/README.md`

#### AnimationBase Interface

```python
class AnimationBase(ABC):
    """
    Base class for frame-based animations.

    Subclasses must implement generate_frame() to produce LED colors.
    The framework calls generate_frame() at target FPS (default: 40).
    """

    # Class attributes (optional)
    ANIMATION_NAME: str = "Animation Name"
    ANIMATION_DESCRIPTION: str = "Description"
    ANIMATION_AUTHOR: str = "Author"
    ANIMATION_VERSION: str = "1.0"

    def __init__(self, controller, config: Dict[str, Any] = None):
        """
        Initialize animation.

        Args:
            controller: LEDController instance
            config: Initial parameter values
        """

    @abstractmethod
    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        """
        Generate one frame of animation.

        Args:
            time_elapsed: Seconds since animation started
            frame_count: Number of frames generated so far

        Returns:
            List of (r, g, b) tuples, length must equal controller.total_leds
        """

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        """
        Define configurable parameters.

        Returns:
            {
                'param_name': {
                    'type': 'float' | 'int' | 'str' | 'bool',
                    'min': <value>,      # for numeric types
                    'max': <value>,      # for numeric types
                    'default': <value>,
                    'description': 'Human-readable description'
                }
            }
        """

    def update_parameters(self, new_params: Dict[str, Any]) -> None:
        """Update parameters in real-time."""

    def get_runtime_stats(self) -> Dict[str, Any]:
        """
        Return animation-specific metrics for debugging.

        Returns:
            Dictionary with any useful debugging information
        """

    def start(self) -> None:
        """Called when animation starts."""

    def stop(self) -> None:
        """Called when animation stops."""

    def cleanup(self) -> None:
        """Called when animation is destroyed."""
```

#### StatefulAnimationBase Interface

```python
class StatefulAnimationBase(AnimationBase):
    """
    Base class for animations that manage their own timing.

    Use this for animations that don't need frame-by-frame generation,
    such as hardware tests that hold states for seconds at a time.
    """

    @abstractmethod
    def run_animation(self) -> None:
        """
        Main animation loop (runs in separate thread).

        Check self.stop_event.is_set() periodically to allow clean shutdown.
        Update LEDs using self.controller.set_all_pixels() as needed.
        """
```

### Web API Contract

**File:** `web/README.md`

#### REST API Endpoints

**System Status**
```
GET /api/status
Response: {
    "is_running": bool,
    "current_animation": str | null,
    "uptime_seconds": float,
    "frame_count": int,
    "fps": float
}
```

**List Animations**
```
GET /api/animations
Response: {
    "animations": [
        {
            "name": str,
            "description": str,
            "author": str,
            "version": str,
            "parameters": {...}
        }
    ]
}
```

**Start Animation**
```
POST /api/animation/start
Body: {
    "name": str,
    "config": {...}  # optional
}
Response: {
    "success": bool,
    "message": str
}
```

**Stop Animation**
```
POST /api/animation/stop
Response: {
    "success": bool
}
```

**Update Parameters**
```
POST /api/animation/params
Body: {
    "param_name": value,
    ...
}
Response: {
    "success": bool
}
```

**Performance Metrics (NEW)**
```
GET /api/metrics
Response: {
    "animation": {
        "frame_generation_ms": float,
        "target_fps": int,
        "actual_fps": float
    },
    "driver": {
        "spi_transfer_ms": float,
        "bytes_per_second": int
    },
    "system": {
        "cpu_percent": float,
        "memory_mb": float
    }
}
```

**Hardware Statistics (NEW)**
```
GET /api/hardware/stats
Response: {
    "devices": [
        {
            "device_id": int,
            "spi_speed_hz": int,
            "frames_sent": int,
            "errors": int,
            "last_frame_ms": float
        }
    ]
}
```

### IPC Contract

**File:** `ipc/README.md`

#### Control Channel Protocol

**Control File Format** (`run_state/control.json`):
```json
{
    "command_id": 1234567890.123,
    "action": "start_animation" | "stop_animation" | "update_params",
    "data": {
        "animation_name": "rainbow",
        "config": {...}
    },
    "written_at": 1234567890.123
}
```

**Status File Format** (`run_state/status.json`):
```json
{
    "is_running": true,
    "current_animation": "rainbow",
    "frame_count": 12345,
    "fps": 42.5,
    "frame_data_encoded": "base64-encoded-compressed-frame",
    "frame_encoding": "json-zlib-base64",
    "metrics": {
        "frame_generation_ms": 5.2,
        "spi_transfer_ms": 3.1
    },
    "written_at": 1234567890.123
}
```

---

## Development Workflow

### Daily Development

1. **Start Development Environment**
   ```bash
   just setup          # First time only
   source .venv/bin/activate
   just start-all      # Start controller + web
   ```

2. **Make Changes**
   - Edit code in appropriate layer
   - Follow interface contracts
   - Add tests for new functionality

3. **Test Changes**
   ```bash
   just test           # Run test suite
   just lint           # Check code quality
   ```

4. **Deploy to Hardware**
   ```bash
   just deploy         # Deploy to Raspberry Pi
   ```

### Creating New Animation

1. **Generate Template**
   ```bash
   cd animation
   just new my_animation
   ```

2. **Implement Animation**
   ```python
   # animation/plugins/my_animation.py
   from animation.core import AnimationBase

   class MyAnimation(AnimationBase):
       ANIMATION_NAME = "My Animation"
       ANIMATION_DESCRIPTION = "Does cool things"

       def generate_frame(self, time_elapsed, frame_count):
           # Return list of (r, g, b) tuples
           return [(255, 0, 0)] * self.controller.total_leds
   ```

3. **Test Animation**
   ```bash
   cd animation
   just validate my_animation
   ```

4. **Use Animation**
   - Restart system or use hot-reload
   - Select from web interface

### Debugging Hardware Issues

1. **Run Diagnostics**
   ```bash
   just diagnose       # Comprehensive hardware test
   ```

2. **Analyze SPI Performance**
   ```bash
   just analyze-spi    # Real-time SPI throughput
   ```

3. **Check Metrics**
   - Visit http://localhost:5000/api/metrics
   - Look for bottlenecks in timing data

### Firmware Development

1. **Build Firmware**
   ```bash
   just firmware-build
   ```

2. **Upload to ESP32**
   ```bash
   just firmware-upload
   ```

3. **Monitor Serial Output**
   ```bash
   just firmware-monitor
   ```

---

## TODO Workflow & Registry

### Process

1. Add TODOs to the Inbox with the template below.
2. During `/refactor`, TODOs are triaged to a phase and ordered with the roadmap.
3. Work proceeds in phase order unless we explicitly agree to override.
4. When a TODO is completed, mark it done and update the Execution Checklist and
   Session Notes.

### TODO Template

```
- [ ] TODO-YYYYMMDD-##: Short title
  - Phase: 1|2|3|4|5|6|Unassigned
  - Priority: P0|P1|P2
  - Acceptance: Clear outcome, test, or validation
  - Notes: Optional context
```

### TODO Inbox

- (empty)

### Planned TODOs

- (empty)

### In Progress

- (empty)

### Done

- (empty)

---

## Session Notes

### Session 2025-12-25: Initial Survey & Planning

**Attendees:** Developer + AI Assistant

**Objectives:**
1. Survey existing codebase
2. Identify dead code and pain points
3. Design refactoring plan
4. Create living documentation

**Findings:**
- Repository has grown organically from POC to production
- ~77 Python files, many in flat structure
- Significant dead code in debugging/ directory (50+ files)
- Core functionality is solid and working
- Main pain points: organization, debugging workflow, observability

**Decisions:**
1. Adopt layered architecture (firmware, drivers, animation, web, ipc)
2. Remove debugging/ directory (archive or delete)
3. Add metrics/observability at each layer
4. Standardize build system with Justfile
5. Consolidate documentation

**Action Items:**
- [ ] Create new directory structure
- [ ] Move files with git mv
- [ ] Update import paths
- [ ] Test end-to-end functionality
- [ ] Remove dead code
- [ ] Implement metrics collection
- [ ] Create Justfiles
- [ ] Write layer README files

**Risks:**
- Breaking existing deployment
- Import path issues
- Lost functionality during refactor

**Mitigation:**
- Test after each phase
- Keep deployment script working
- Use git branches for major changes

---

## Open Questions & Assumptions

### Open Questions

1. **Q:** Should we keep water_simulation.py and water_simulation_server.py?
   **A:** TBD - Need to check if they're used or if fluid_tank.py replaces them

2. **Q:** What is the purpose of extract_frame_payload.py?
   **A:** TBD - Appears to be a utility script, need to verify usage

3. **Q:** Are all animations in animations/ directory active?
   **A:** TBD - Need to verify which are used in production

4. **Q:** Should we use pytest or another test framework?
   **A:** TBD - No existing tests found, need to choose framework

5. **Q:** What's the target deployment environment? (Raspberry Pi model, OS version)
   **A:** TBD - Appears to be Raspberry Pi with Raspbian/Debian, need to document

6. **Q:** Should IPC remain file-based or move to sockets/queues?
   **A:** TBD - File-based works but adds latency, evaluate alternatives

7. **Q:** What's the acceptable end-to-end latency for UI → LED update?
   **A:** TBD - Need to measure current performance and set targets

### Assumptions

1. **Assumption:** Current system is working in production
   **Validation:** Verify with user before major changes

2. **Assumption:** 2 ESP32 devices × 8 strips × 140 LEDs = 2,240 total LEDs
   **Validation:** Confirmed in led_layout.py

3. **Assumption:** SPI Mode 3 (CPOL=1, CPHA=1) is required
   **Validation:** Confirmed in firmware and driver code

4. **Assumption:** Target FPS is 40
   **Validation:** Confirmed in animation_manager.py

5. **Assumption:** File-based IPC is intentional for process isolation
   **Validation:** Appears to separate web UI from hardware control

6. **Assumption:** Most debugging/ scripts are obsolete from hardware bring-up
   **Validation:** Need to confirm with user

7. **Assumption:** Python 3.10+ is available on target system
   **Validation:** Confirmed in diagnostics output (Python 3.10.5)

8. **Assumption:** PlatformIO is used for firmware development
   **Validation:** Confirmed by platformio.ini presence

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2025-12-25 | Use layered architecture | Clear separation of concerns, easier debugging |
| 2025-12-25 | Keep file-based IPC | Process isolation is valuable, latency is acceptable |
| 2025-12-25 | Add metrics at each layer | Essential for debugging throughput issues |
| 2025-12-25 | Use Justfile for build automation | Simple, powerful, cross-platform |
| 2025-12-25 | Consolidate docs in docs/ | Single source of truth |

---

## Next Steps

### Immediate (Before Next Session)

1. **Get User Feedback** on this refactoring plan
2. **Verify Assumptions** about production usage
3. **Identify Critical Paths** that must not break
4. **Backup Current State** before starting refactor

### Phase 1 Execution (After Approval)

1. Create new directory structure
2. Move files with git mv (preserve history)
3. Update all import paths
4. Test end-to-end functionality
5. Update deployment script
6. Commit and tag as "refactor-phase-1"

### Future Considerations

- **Testing Strategy:** Add unit and integration tests
- **CI/CD:** Automated testing and deployment
- **Monitoring:** Real-time performance dashboard
- **Configuration Management:** Environment-specific configs
- **Error Handling:** Standardized error reporting
- **Logging:** Structured logging across all components

---

## Appendix

### Useful Commands

```bash
# Find all Python imports
grep -r "^import\|^from" --include="*.py" .

# Count lines of code
find . -name "*.py" -not -path "./.venv/*" -not -path "./.pio/*" | xargs wc -l

# Find TODO comments
grep -r "TODO\|FIXME\|XXX" --include="*.py" .

# Check for unused imports
.venv/bin/pip install autoflake
.venv/bin/autoflake --check --remove-all-unused-imports -r .
```

### References

- [FastLED Documentation](https://github.com/FastLED/FastLED)
- [ESP32 SPI Slave](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/peripherals/spi_slave.html)
- [Flask Documentation](https://flask.palletsprojects.com/)
- [Just Command Runner](https://github.com/casey/just)

---

**Document Version:** 1.0
**Last Reviewed:** 2025-12-25
**Next Review:** After Phase 1 completion
```
