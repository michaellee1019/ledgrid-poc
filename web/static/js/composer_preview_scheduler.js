/* Bounded, two-lane scheduler for Composer's inert final preview. */
(() => {
  'use strict';
  class ComposerPreviewScheduler {
    constructor({request, onFrame, onError, isVisible = () => true, intervalMs = 34, setIntervalFn = (callback, delay) => globalThis.setInterval(callback, delay), clearIntervalFn = (timer) => globalThis.clearInterval(timer)}) {
      this.request = request;
      this.onFrame = onFrame;
      this.onError = onError;
      this.isVisible = isVisible;
      this.intervalMs = intervalMs;
      this.setIntervalFn = setIntervalFn;
      this.clearIntervalFn = clearIntervalFn;
      this.authoredGeneration = 0;
      this.authorityEpoch = 0;
      this.authoredInFlight = false;
      this.authoredPending = null;
      this.pollInFlight = false;
      this.polling = false;
      this.available = true;
      this.timer = null;
      this.candidate = null;
    }

    start(candidate) {
      this.candidate = candidate;
      this.polling = true;
      this._arm();
    }

    resume() {
      this.available = true;
      this._arm();
    }

    suspend(error) {
      this.available = false;
      if (this.timer !== null) this.clearIntervalFn(this.timer);
      this.timer = null;
      return error;
    }

    submitAuthored(candidate, options = {}) {
      this.resume();
      const generation = options.generation || ++this.authoredGeneration;
      this.authoredGeneration = Math.max(this.authoredGeneration, generation);
      const authorityEpoch = ++this.authorityEpoch;
      if (this.authoredPending) this.authoredPending.resolve(null);
      return new Promise((resolve, reject) => {
        this.authoredPending = {candidate, generation, authorityEpoch, autosave: options.autosave !== false, resolve, reject};
        this._drainAuthored();
      });
    }

    poll() {
      if (!this.polling || !this.available || !this.isVisible() || this.pollInFlight || this.authoredInFlight || this.authoredPending || typeof this.candidate !== 'function') return;
      const generation = this.authoredGeneration;
      const authorityEpoch = ++this.authorityEpoch;
      this.pollInFlight = true;
      Promise.resolve(this.request(this.candidate())).then((body) => {
        if (this.available && this.isVisible() && authorityEpoch === this.authorityEpoch && !this.authoredInFlight && !this.authoredPending) this.onFrame(body, {kind: 'poll', generation, authorityEpoch, autosave: false});
      }).catch((error) => {
        if (!this.isVisible() || authorityEpoch !== this.authorityEpoch || this.authoredInFlight || this.authoredPending) return;
        if (error && error.previewUnavailable) this.suspend(error);
        this.onError(error, {kind: 'poll', generation, authorityEpoch});
      }).finally(() => { this.pollInFlight = false; });
    }

    _arm() {
      if (!this.polling || !this.available || this.timer !== null) return;
      this.timer = this.setIntervalFn(() => this.poll(), this.intervalMs);
    }

    _drainAuthored() {
      if (this.authoredInFlight || !this.authoredPending) return;
      const task = this.authoredPending;
      this.authoredPending = null;
      this.authoredInFlight = true;
      Promise.resolve(this.request(task.candidate)).then((body) => {
        if (task.authorityEpoch === this.authorityEpoch) {
          this.onFrame(body, {kind: 'authored', generation: task.generation, authorityEpoch: task.authorityEpoch, autosave: task.autosave, candidate: task.candidate});
          this.resume();
          task.resolve(body);
        } else task.resolve(null);
      }).catch((error) => {
        if (task.authorityEpoch === this.authorityEpoch) {
          if (error && error.previewUnavailable) this.suspend(error);
          this.onError(error, {kind: 'authored', generation: task.generation, authorityEpoch: task.authorityEpoch});
          task.reject(error);
        } else task.resolve(null);
      }).finally(() => {
        this.authoredInFlight = false;
        this._drainAuthored();
      });
    }
  }
  if (typeof module === 'object' && module.exports) module.exports = {ComposerPreviewScheduler};
  else window.ComposerPreviewScheduler = ComposerPreviewScheduler;
})();
