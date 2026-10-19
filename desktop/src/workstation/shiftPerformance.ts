/** Two seconds below 24fps after warm-up, ignoring time spent in hidden tabs. */
export class ShiftPerformance {
  private start: number | null = null;
  private frames = 0;
  private warm = true;

  sample(now: number, visible: boolean): boolean {
    if (!visible || this.start === null) {
      this.start = now;
      this.frames = 0;
      return false;
    }
    this.frames++;
    const elapsed = now - this.start;
    if (elapsed < (this.warm ? 1000 : 2000)) return false;
    const low = !this.warm && this.frames * 1000 / elapsed < 24;
    this.warm = false;
    this.start = now;
    this.frames = 0;
    return low;
  }
}
