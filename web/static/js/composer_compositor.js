(function attachLEDGridComposerCompositor(root, factory) {
    'use strict';

    const api = Object.freeze(factory());
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root && typeof root === 'object') {
        root.LEDGridComposerCompositor = api;
    }
})(typeof globalThis === 'object' ? globalThis : this, function compositorFactory() {
    'use strict';

    const RGB = 'rgb';
    const PREMULTIPLIED_RGBA = 'premultiplied-rgba';
    const REPLACE = 'replace';
    const SOURCE_OVER = 'source-over';
    const KEYED = 'keyed';
    const CHANNEL_MAX = 255;

    class ComposerCompositorError extends Error {
        constructor(message) {
            super(message);
            this.name = 'ComposerCompositorError';
        }
    }

    function positiveInteger(value, name) {
        if (!Number.isSafeInteger(value) || value <= 0) {
            throw new ComposerCompositorError(`${name} must be a positive integer`);
        }
        return value;
    }

    function opacityValue(value, layerIndex) {
        const resolved = value === undefined ? CHANNEL_MAX : value;
        if (!Number.isInteger(resolved) || resolved < 0 || resolved > CHANNEL_MAX) {
            throw new ComposerCompositorError(
                `layers[${layerIndex}].opacity must be an integer from 0 to 255`
            );
        }
        return resolved;
    }

    function byteView(value, name) {
        if (value instanceof Uint8Array) {
            return value;
        }
        if (value instanceof ArrayBuffer) {
            return new Uint8Array(value);
        }
        if (typeof SharedArrayBuffer === 'function' && value instanceof SharedArrayBuffer) {
            return new Uint8Array(value);
        }
        if (ArrayBuffer.isView(value) && value.BYTES_PER_ELEMENT === 1) {
            return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
        }
        throw new ComposerCompositorError(`${name} must be an ArrayBuffer or byte array`);
    }

    function keyValue(value, layerIndex) {
        const resolved = value === undefined ? [0, 0, 0] : value;
        if (!Array.isArray(resolved) && !(resolved instanceof Uint8Array)) {
            throw new ComposerCompositorError(`layers[${layerIndex}].key must contain three bytes`);
        }
        if (
            resolved.length !== 3
            || Array.from(resolved).some((channel) => (
                !Number.isInteger(channel) || channel < 0 || channel > CHANNEL_MAX
            ))
        ) {
            throw new ComposerCompositorError(`layers[${layerIndex}].key must contain three bytes`);
        }
        return Array.from(resolved);
    }

    // Version 1 host/receiver contract: integer product with round-half-up.
    function roundU8Product(value, factor) {
        return Math.floor((value * factor + 127) / CHANNEL_MAX);
    }

    function sourceOverRgb(output, outputOffset, red, green, blue, alpha) {
        const inverseAlpha = CHANNEL_MAX - alpha;
        output[outputOffset] = Math.min(
            CHANNEL_MAX,
            red + roundU8Product(output[outputOffset], inverseAlpha),
        );
        output[outputOffset + 1] = Math.min(
            CHANNEL_MAX,
            green + roundU8Product(output[outputOffset + 1], inverseAlpha),
        );
        output[outputOffset + 2] = Math.min(
            CHANNEL_MAX,
            blue + roundU8Product(output[outputOffset + 2], inverseAlpha),
        );
    }

    function validateLayer(rawLayer, index, pixelCount) {
        if (!rawLayer || typeof rawLayer !== 'object') {
            throw new ComposerCompositorError(`layers[${index}] must be an object`);
        }
        if (rawLayer.enabled !== undefined && typeof rawLayer.enabled !== 'boolean') {
            throw new ComposerCompositorError(`layers[${index}].enabled must be a boolean`);
        }
        const enabled = rawLayer.enabled !== false;
        const opacity = opacityValue(rawLayer.opacity, index);
        const format = rawLayer.format;
        if (format !== RGB && format !== PREMULTIPLIED_RGBA) {
            throw new ComposerCompositorError(
                `layers[${index}].format must be '${RGB}' or '${PREMULTIPLIED_RGBA}'`
            );
        }
        const channels = format === RGB ? 3 : 4;
        const pixels = byteView(rawLayer.pixels, `layers[${index}].pixels`);
        const expectedLength = pixelCount * channels;
        if (pixels.byteLength !== expectedLength) {
            throw new ComposerCompositorError(
                `layers[${index}] must contain ${expectedLength} bytes, got ${pixels.byteLength}`
            );
        }
        const defaultBlend = format === RGB ? REPLACE : SOURCE_OVER;
        const blend = rawLayer.blend ?? defaultBlend;
        if (format === RGB && blend !== REPLACE && blend !== KEYED) {
            throw new ComposerCompositorError(
                `RGB layers support '${REPLACE}' or '${KEYED}' blending`
            );
        }
        if (format === PREMULTIPLIED_RGBA && blend !== SOURCE_OVER) {
            throw new ComposerCompositorError(
                `premultiplied RGBA layers require '${SOURCE_OVER}' blending`
            );
        }
        const key = blend === KEYED ? keyValue(rawLayer.key, index) : null;
        if (format === PREMULTIPLIED_RGBA) {
            for (let offset = 0; offset < pixels.length; offset += 4) {
                const alpha = pixels[offset + 3];
                if (
                    pixels[offset] > alpha
                    || pixels[offset + 1] > alpha
                    || pixels[offset + 2] > alpha
                ) {
                    throw new ComposerCompositorError(
                        `layers[${index}] must contain premultiplied RGBA; pixel ${offset / 4} has RGB greater than alpha`
                    );
                }
            }
        }
        return {blend, enabled, format, key, opacity, pixels};
    }

    function compositeRgb(output, layer, pixelCount) {
        const opacity = layer.opacity;
        const inverseOpacity = CHANNEL_MAX - opacity;
        const key = layer.key;
        for (let pixel = 0; pixel < pixelCount; pixel += 1) {
            const inputOffset = pixel * 3;
            if (
                key
                && layer.pixels[inputOffset] === key[0]
                && layer.pixels[inputOffset + 1] === key[1]
                && layer.pixels[inputOffset + 2] === key[2]
            ) {
                continue;
            }
            if (opacity === CHANNEL_MAX) {
                output[inputOffset] = layer.pixels[inputOffset];
                output[inputOffset + 1] = layer.pixels[inputOffset + 1];
                output[inputOffset + 2] = layer.pixels[inputOffset + 2];
                continue;
            }
            output[inputOffset] = Math.min(
                CHANNEL_MAX,
                roundU8Product(layer.pixels[inputOffset], opacity)
                    + roundU8Product(output[inputOffset], inverseOpacity),
            );
            output[inputOffset + 1] = Math.min(
                CHANNEL_MAX,
                roundU8Product(layer.pixels[inputOffset + 1], opacity)
                    + roundU8Product(output[inputOffset + 1], inverseOpacity),
            );
            output[inputOffset + 2] = Math.min(
                CHANNEL_MAX,
                roundU8Product(layer.pixels[inputOffset + 2], opacity)
                    + roundU8Product(output[inputOffset + 2], inverseOpacity),
            );
        }
    }

    function compositePremultipliedRgba(output, layer, pixelCount) {
        for (let pixel = 0; pixel < pixelCount; pixel += 1) {
            const inputOffset = pixel * 4;
            const outputOffset = pixel * 3;
            let red = layer.pixels[inputOffset];
            let green = layer.pixels[inputOffset + 1];
            let blue = layer.pixels[inputOffset + 2];
            let alpha = layer.pixels[inputOffset + 3];
            if (layer.opacity !== CHANNEL_MAX) {
                red = roundU8Product(red, layer.opacity);
                green = roundU8Product(green, layer.opacity);
                blue = roundU8Product(blue, layer.opacity);
                alpha = roundU8Product(alpha, layer.opacity);
            }
            if (alpha === 0) {
                continue;
            }
            sourceOverRgb(output, outputOffset, red, green, blue, alpha);
        }
    }

    /**
     * Compose ordered bottom-to-top browser-rendered layers.
     *
     * Every input and output uses canonical strip-major order:
     * `pixel = strip * height + led`. RGB layers are opaque replace planes or
     * exact-color-keyed compatibility foregrounds. The authoritative overlay
     * contract is premultiplied RGBA source-over, byte-identical to the host.
     */
    function composeLayers({width, height, layers}) {
        const resolvedWidth = positiveInteger(width, 'width');
        const resolvedHeight = positiveInteger(height, 'height');
        if (!Array.isArray(layers)) {
            throw new ComposerCompositorError('layers must be an array');
        }
        const pixelCount = resolvedWidth * resolvedHeight;
        if (!Number.isSafeInteger(pixelCount)) {
            throw new ComposerCompositorError('width × height exceeds the safe pixel count');
        }
        const validated = layers.map((layer, index) => validateLayer(layer, index, pixelCount));
        const output = new Uint8Array(pixelCount * 3);
        for (const layer of validated) {
            if (!layer.enabled || layer.opacity === 0) {
                continue;
            }
            if (layer.format === RGB) {
                compositeRgb(output, layer, pixelCount);
            } else {
                compositePremultipliedRgba(output, layer, pixelCount);
            }
        }
        return output;
    }

    return {
        ComposerCompositorError,
        composeLayers,
        roundU8Product,
    };
});

