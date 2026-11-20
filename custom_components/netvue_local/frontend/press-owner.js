// Tracks which input (one pointer or the keyboard) owns the current press.
export class PressOwner {
  constructor() {
    this._owner = null;
  }

  get active() {
    return this._owner !== null;
  }

  begin(owner) {
    if (this._owner !== null) {
      return false;
    }
    this._owner = owner;
    return true;
  }

  // True only when `owner` is the one that started the press.
  end(owner) {
    if (this._owner !== owner) {
      return false;
    }
    this._owner = null;
    return true;
  }

  clear() {
    this._owner = null;
  }
}

export const KEYBOARD = "keyboard";
