export function isImeKey(event: KeyboardEvent) {
  // Some IMEs report the final composition key as 229 after isComposing becomes false.
  return event.isComposing || event.keyCode === 229
}
