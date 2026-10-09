idf_component_register(SRCS "main.cpp" "protocol.cpp" "parallel_led_driver.cpp" "ws2812_encoder.cpp" INCLUDE_DIRS "../include" PRIV_REQUIRES esp_timer esp_driver_gpio esp_driver_spi esp_lcd)
