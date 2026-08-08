#include "ledgrid/startup_animation.hpp"
#include "ledgrid/native_examples.hpp"

namespace ledgrid {

bool render_startup_rainbow(
    std::uint64_t elapsed_us,
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip,
    std::uint8_t* output,
    std::size_t output_size) {
  if (output == nullptr || strip_count == 0 || leds_per_strip == 0) {
    return false;
  }
  ledgrid_render_context_v1 context{};
  context.abi_version = LEDGRID_ANIMATION_ABI_V1;
  context.local_strips = strip_count;
  context.leds_per_strip = leds_per_strip;
  context.elapsed_us = elapsed_us;
  context.scaled_elapsed_us = elapsed_us;
  context.rgb_output = output;
  context.rgb_output_size = output_size;
  void* state = nullptr;
  const ledgrid_animation_callbacks_v1* callbacks =
      ledgrid_builtin_startup_rainbow_v1();
  if (callbacks == nullptr || callbacks->abi_version != LEDGRID_ANIMATION_ABI_V1 ||
      callbacks->initialize == nullptr || callbacks->render == nullptr ||
      callbacks->cleanup == nullptr ||
      callbacks->initialize(&context, nullptr, &state) != LEDGRID_ANIMATION_OK)
    return false;
  const bool rendered =
      callbacks->render(state, &context) == LEDGRID_ANIMATION_OK;
  callbacks->cleanup(state);
  return rendered;
}

}  // namespace ledgrid
