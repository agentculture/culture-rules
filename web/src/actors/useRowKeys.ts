import { useRef } from "react";

let counter = 0;
const nextKey = () => `row-${++counter}`;

/**
 * Stable React keys for editable list rows whose values carry no id of their
 * own (event names, argv tokens, headers). A key is minted when a row first
 * appears and follows it when rows before it are removed, so React keeps each
 * row's DOM (and focus) with the right value.
 *
 * `keys(list, count)` returns one key per row of the list named `list`;
 * call `drop(list, i)` alongside the state update that removes row `i`.
 * Lists are named by path so nested rows (a command's tokens) get their own.
 */
export function useRowKeys() {
  const store = useRef(new Map<string, string[]>());

  const keys = (list: string, count: number): string[] => {
    const current = store.current.get(list) ?? [];
    while (current.length < count) current.push(nextKey());
    current.length = count;
    store.current.set(list, current);
    return current;
  };

  const drop = (list: string, index: number) => {
    store.current.get(list)?.splice(index, 1);
  };

  return { keys, drop };
}
