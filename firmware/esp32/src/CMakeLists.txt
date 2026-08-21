file(GLOB_RECURSE app_sources "*.cpp")
idf_component_register(SRCS ${app_sources} INCLUDE_DIRS "../include")

# PlatformIO does not forward ordinary build_flags into ESP-IDF component
# compilation. Keep the rollout distinction here where the compile command is
# auditable: production is off, and only the named canary environment is on.
if("$ENV{LEDGRID_LOCAL_BACKGROUND}" STREQUAL "1")
  target_compile_definitions(${COMPONENT_LIB} PRIVATE
    LEDGRID_ENABLE_LOCAL_BACKGROUND=1)
else()
  target_compile_definitions(${COMPONENT_LIB} PRIVATE
    LEDGRID_ENABLE_LOCAL_BACKGROUND=0)
endif()

if("$ENV{LEDGRID_INSTALLATION_PROFILES}" STREQUAL "1")
  target_compile_definitions(${COMPONENT_LIB} PRIVATE
    LEDGRID_ENABLE_INSTALLATION_PROFILES=1)
else()
  target_compile_definitions(${COMPONENT_LIB} PRIVATE
    LEDGRID_ENABLE_INSTALLATION_PROFILES=0)
endif()
