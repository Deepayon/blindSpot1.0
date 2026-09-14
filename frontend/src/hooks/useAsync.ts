/** Small data-loading hooks. Deliberately dependency-free, the POC does not
 *  need a data-fetching library, and one more abstraction would obscure the
 *  request/response shape a reviewer wants to see. */
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../services/api";

export interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Runs `loader` on mount and whenever `deps` change. */
export function useAsync<T>(loader: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    let current = true;
    setLoading(true);
    loader()
      .then((result) => {
        if (current && alive.current) {
          setData(result);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (current && alive.current) {
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (current && alive.current) setLoading(false);
      });
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, reload };
}

export interface ActionState<TArgs extends unknown[], TResult> {
  run: (...args: TArgs) => Promise<TResult | null>;
  result: TResult | null;
  error: string | null;
  pending: boolean;
  reset: () => void;
}

/** Wraps a one-shot action (submit, upload) with pending/error state. */
export function useAction<TArgs extends unknown[], TResult>(
  action: (...args: TArgs) => Promise<TResult>,
): ActionState<TArgs, TResult> {
  const [result, setResult] = useState<TResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const run = useCallback(
    async (...args: TArgs) => {
      setPending(true);
      setError(null);
      try {
        const value = await action(...args);
        setResult(value);
        return value;
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : String(err));
        return null;
      } finally {
        setPending(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const reset = useCallback(() => {
    setResult(null);
    setError(null);
  }, []);

  return { run, result, error, pending, reset };
}

/** The feature names present in the indexed suite, most-tested first.
 *
 *  Read from the corpus rather than hard-coded. A fixed list of retail-shaped
 *  names (Checkout, Payments, Orders) is wrong for an insurance or logistics
 *  suite, and offering a filter for features that do not exist while omitting
 *  the ones that do makes the screen actively misleading.
 */
export function useIndexedFeatures(): string[] {
  const [features, setFeatures] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    api
      .testStats()
      .then((stats) => {
        if (cancelled) return;
        const counts = (stats.by_feature ?? {}) as Record<string, number>;
        setFeatures(
          Object.entries(counts)
            .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
            .map(([name]) => name),
        );
      })
      // The filter is a convenience; losing it must not break the screen.
      .catch(() => {
        if (!cancelled) setFeatures([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return features;
}

/** Hash-based routing. Avoids a router dependency for five screens. */
export function useHashRoute(): [string, (route: string) => void] {
  const read = () => window.location.hash.replace(/^#\/?/, "") || "dashboard";
  const [route, setRoute] = useState(read);

  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const navigate = useCallback((next: string) => {
    window.location.hash = `/${next}`;
  }, []);

  return [route, navigate];
}
