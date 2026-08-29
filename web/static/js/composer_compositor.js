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
    const LGIP_FIXED_HEADER_BYTES = 112;
    const LGIP_SECTION_ENTRY_BYTES = 24;
    const LGIP_SECTION_COUNT = 9;
    const LGIP_PROFILE_HEADER_BYTES = (
        LGIP_FIXED_HEADER_BYTES + LGIP_SECTION_ENTRY_BYTES * LGIP_SECTION_COUNT
    );
    const LGIP_CONTENT_DIGEST_OFFSET = 68;
    const LGIP_CONTENT_DIGEST_BYTES = 32;
    const FOLIAGE = 1;
    const GLOBE = 2;

    const FOREGROUND_TINTS = Object.freeze({
        foliageCore: Object.freeze({alpha: 224, rgb: Object.freeze([4, 18, 8])}),
        foliageEdge: Object.freeze({alpha: 216, rgb: Object.freeze([20, 78, 38])}),
        globeCore: Object.freeze({alpha: 146, rgb: Object.freeze([52, 34, 16])}),
        globeEdge: Object.freeze({alpha: 184, rgb: Object.freeze([178, 112, 42])}),
    });

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

    function hexBytes(value) {
        return Array.from(value, (byte) => byte.toString(16).padStart(2, '0')).join('');
    }

    function decodeInstallationProfile(value, expectedDigest) {
        const bytes = byteView(value, 'installation profile');
        const digest = String(expectedDigest || '').toLowerCase();
        if (!/^[0-9a-f]{64}$/.test(digest) || /^0+$/.test(digest)) {
            throw new ComposerCompositorError(
                'installation profile digest must be a non-empty SHA-256 identity',
            );
        }
        if (bytes.byteLength < LGIP_PROFILE_HEADER_BYTES || bytes.byteLength > 65535) {
            throw new ComposerCompositorError('installation profile has an invalid byte count');
        }
        if (String.fromCharCode(...bytes.subarray(0, 4)) !== 'LGIP') {
            throw new ComposerCompositorError('installation profile is not LGIP');
        }
        const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        const exactHeader = [
            [view.getUint16(4, false), 1, 'format version'],
            [view.getUint16(6, false), LGIP_FIXED_HEADER_BYTES, 'fixed header size'],
            [view.getUint32(8, false), 0, 'flags'],
            [view.getUint16(12, false), 33, 'global strip count'],
            [view.getUint16(14, false), 138, 'LED height'],
            [view.getUint16(16, false), 0, 'strip origin'],
            [view.getUint16(18, false), 33, 'represented strip count'],
            [view.getUint32(20, false), 4554, 'pixel count'],
            [view.getUint8(25), 7, 'globe-region count'],
            [view.getUint16(26, false), LGIP_SECTION_COUNT, 'section count'],
            [view.getUint16(28, false), LGIP_SECTION_ENTRY_BYTES, 'section entry size'],
            [view.getUint32(32, false), bytes.byteLength, 'declared byte count'],
        ];
        for (const [actual, expected, label] of exactHeader) {
            if (actual !== expected) {
                throw new ComposerCompositorError(`installation profile ${label} is invalid`);
            }
        }
        const embeddedDigest = hexBytes(bytes.subarray(
            LGIP_CONTENT_DIGEST_OFFSET,
            LGIP_CONTENT_DIGEST_OFFSET + LGIP_CONTENT_DIGEST_BYTES,
        ));
        if (embeddedDigest !== digest) {
            throw new ComposerCompositorError(
                'installation profile content identity does not match the verified renderer profile',
            );
        }

        const sections = [];
        let expectedOffset = LGIP_PROFILE_HEADER_BYTES;
        for (let position = 0; position < LGIP_SECTION_COUNT; position += 1) {
            const entry = LGIP_FIXED_HEADER_BYTES + position * LGIP_SECTION_ENTRY_BYTES;
            const sectionId = view.getUint16(entry, false);
            const elementWidth = view.getUint8(entry + 3);
            const elementCount = view.getUint32(entry + 4, false);
            const offset = view.getUint32(entry + 8, false);
            const length = view.getUint32(entry + 12, false);
            const reserved = view.getUint32(entry + 20, false);
            if (
                sectionId !== position + 1
                || elementWidth !== 1
                || elementCount !== 4554
                || length !== 4554
                || offset !== expectedOffset
                || reserved !== 0
                || offset + length > bytes.byteLength
            ) {
                throw new ComposerCompositorError(
                    `installation profile section ${position + 1} is invalid`,
                );
            }
            sections.push(bytes.slice(offset, offset + length));
            expectedOffset = offset + length;
        }
        if (expectedOffset !== bytes.byteLength) {
            throw new ComposerCompositorError(
                'installation profile contains trailing or unreferenced bytes',
            );
        }

        const category = sections[0];
        const foliageEdge = sections[2];
        const globeEdge = sections[3];
        const globeRegion = sections[5];
        let foliagePixels = 0;
        let globePixels = 0;
        for (let pixel = 0; pixel < category.length; pixel += 1) {
            if (category[pixel] === FOLIAGE) foliagePixels += 1;
            else if (category[pixel] === GLOBE) globePixels += 1;
            else if (category[pixel] !== 0) {
                throw new ComposerCompositorError('installation profile category is invalid');
            }
            if (
                foliageEdge[pixel] > 1
                || globeEdge[pixel] > 1
                || globeRegion[pixel] > 7
                || (category[pixel] === GLOBE) !== (globeRegion[pixel] !== 0)
            ) {
                throw new ComposerCompositorError(
                    'installation profile foreground geometry is inconsistent',
                );
            }
        }
        return Object.freeze({
            digest,
            width: 33,
            height: 138,
            category,
            foliageEdge,
            globeEdge,
            globeRegion,
            foliagePixels,
            globePixels,
        });
    }

    function tintChannel(source, tint, alpha) {
        return Math.floor((source * (CHANNEL_MAX - alpha) + tint * alpha + 127) / CHANNEL_MAX);
    }

    /**
     * Add calibrated physical occlusion to image-style RGBA presentation bytes
     * without changing the canonical renderer frame used for Check or activation.
     */
    function applyInstallationForeground({width, height, rgba, profile}) {
        const resolvedWidth = positiveInteger(width, 'width');
        const resolvedHeight = positiveInteger(height, 'height');
        if (
            !profile
            || profile.width !== resolvedWidth
            || profile.height !== resolvedHeight
            || !(profile.category instanceof Uint8Array)
        ) {
            throw new ComposerCompositorError(
                'installation foreground profile must match the preview geometry',
            );
        }
        const input = byteView(rgba, 'rgba');
        if (input.byteLength !== resolvedWidth * resolvedHeight * 4) {
            throw new ComposerCompositorError(
                `rgba must contain ${resolvedWidth * resolvedHeight * 4} bytes`,
            );
        }
        const output = new Uint8ClampedArray(input);
        for (let strip = 0; strip < resolvedWidth; strip += 1) {
            for (let led = 0; led < resolvedHeight; led += 1) {
                const profilePixel = strip * resolvedHeight + led;
                const category = profile.category[profilePixel];
                if (category === 0) continue;
                const tint = category === FOLIAGE
                    ? (profile.foliageEdge[profilePixel]
                        ? FOREGROUND_TINTS.foliageEdge
                        : FOREGROUND_TINTS.foliageCore)
                    : (profile.globeEdge[profilePixel]
                        ? FOREGROUND_TINTS.globeEdge
                        : FOREGROUND_TINTS.globeCore);
                const rgbaOffset = (
                    (resolvedHeight - 1 - led) * resolvedWidth + strip
                ) * 4;
                output[rgbaOffset] = tintChannel(output[rgbaOffset], tint.rgb[0], tint.alpha);
                output[rgbaOffset + 1] = tintChannel(
                    output[rgbaOffset + 1], tint.rgb[1], tint.alpha,
                );
                output[rgbaOffset + 2] = tintChannel(
                    output[rgbaOffset + 2], tint.rgb[2], tint.alpha,
                );
                output[rgbaOffset + 3] = CHANNEL_MAX;
            }
        }
        return output;
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
        applyInstallationForeground,
        ComposerCompositorError,
        composeLayers,
        decodeInstallationProfile,
        roundU8Product,
    };
});
