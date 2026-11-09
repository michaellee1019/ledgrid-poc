'use strict';

const wallStatus = document.querySelector('#wall-status');
const playButton = document.querySelector('#play-tetris');
const observationUrl = '/api/v1/composer/settings/observed';

async function requestJson(url, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2000);
    try {
        const response = await fetch(url, {...options, cache: 'no-store', signal: controller.signal});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'The wall is unavailable.');
        return data;
    } finally {
        clearTimeout(timeout);
    }
}

async function startTetris(basis) {
    playButton.disabled = true;
    wallStatus.textContent = 'Waiting for the wall controller…';
    try {
        const request = await requestJson('/api/v1/tetris/play', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(basis),
        });
        const deadline = performance.now() + 5500;
        while (performance.now() < deadline) {
            const observation = await requestJson(
                `${observationUrl}?request_id=${encodeURIComponent(request.request_id)}`
            );
            const result = observation.command_result;
            if (result?.request_id === request.request_id) {
                if (result.state === 'completed') {
                    wallStatus.textContent = 'Tetris player mode started.';
                    return;
                }
                if (result.state === 'failed') throw new Error(result.error || 'The wall rejected the request.');
            }
            await new Promise(resolve => setTimeout(resolve, 250));
        }
        wallStatus.textContent = 'The wall did not confirm the request. Reload to check playback.';
    } catch (error) {
        wallStatus.textContent = `${error.message} Reload wall status to try again.`;
    }
}

requestJson(observationUrl).then(status => {
    if (status.freshness !== 'fresh' || !status.controller_session_id ||
        !Number.isInteger(status.controller_state_revision)) {
        throw new Error('Current wall status is unavailable.');
    }
    const animation = status.scene?.animation?.component_id?.replaceAll('_', ' ');
    wallStatus.textContent = status.is_running ? `Now playing ${animation || 'on the wall'}.` : 'The wall is idle.';
    const basis = {
        expected_controller_session_id: status.controller_session_id,
        expected_controller_state_revision: status.controller_state_revision,
    };
    playButton.addEventListener('click', () => startTetris(basis), {once: true});
    playButton.disabled = false;
}).catch(error => {
    wallStatus.textContent = `${error.message} Reload wall status to try again.`;
});
