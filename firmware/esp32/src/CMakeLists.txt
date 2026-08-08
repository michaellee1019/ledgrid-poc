file(GLOB_RECURSE app_sources "*.cpp")
idf_component_register(SRCS ${app_sources} INCLUDE_DIRS "../include")
target_compile_definitions(${COMPONENT_LIB} PRIVATE LEDGRID_BUILTIN_NATIVE_EXAMPLES=1)
