#pragma once

#include <cstddef>
#include <cstdint>

namespace ledgrid {

void sha256(const std::uint8_t* data, std::size_t size, std::uint8_t output[32]);

}  // namespace ledgrid
