// Periodic reads stop while the page is hidden. Never overlap a slow refresh.
export class VisibleInterval {
  constructor(callback, milliseconds, onResume = callback, page = document) {
    this.callback = callback;
    this.onResume = onResume;
    this.milliseconds = milliseconds;
    this.page = page;
    this.pending = false;
    this.resumePending = false;
    this.stopped = false;
    this.changed = () => {
      clearInterval(this.timer);
      if (!this.page.hidden && !this.stopped) {
        this.timer = setInterval(() => this.run(), this.milliseconds);
        this.run(true);
      }
    };
    page.addEventListener('visibilitychange', this.changed);
    if (!page.hidden) {
      this.timer = setInterval(() => this.run(), milliseconds);
    }
  }

  run(resume = false) {
    if (this.stopped || this.page.hidden) {
      return;
    }
    if (this.pending) {
      this.resumePending ||= resume;
      return;
    }
    this.pending = true;
    Promise.resolve()
      .then(() => {
        if (!this.stopped && !this.page.hidden) {
          return resume ? this.onResume() : this.callback();
        }
        return null;
      })
      .catch((error) => console.error(error))
      .finally(() => {
        this.pending = false;
        if (this.resumePending) {
          this.resumePending = false;
          this.run(true);
        }
      });
  }

  stop() {
    this.stopped = true;
    clearInterval(this.timer);
    this.page.removeEventListener('visibilitychange', this.changed);
  }
}
