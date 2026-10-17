import type { Adapter, Snapshot } from "./types";
import { createDemoAdapter } from "./fixtures/demo";

declare global {
  interface Window {
    qt?: { webChannelTransport: unknown };
    QWebChannel?: new (
      transport: unknown,
      callback: (channel: any) => void,
    ) => void;
  }
}

export async function connect(): Promise<Adapter> {
  if (!window.qt?.webChannelTransport || !window.QWebChannel)
    return createDemoAdapter();
  const bridge = await new Promise<any>(
    (resolve) =>
      new window.QWebChannel!(window.qt!.webChannelTransport, (channel) =>
        resolve(channel.objects.bridge),
      ),
  );
  const listeners = new Set<() => void>();
  bridge.message.connect(() => listeners.forEach((listener) => listener()));
  return {
    demo: false,
    snapshot: () =>
      new Promise<Snapshot>((resolve) =>
        bridge.getSnapshot((raw: string) => resolve(JSON.parse(raw))),
      ),
    invoke: (action, payload) =>
      new Promise((resolve, reject) =>
        bridge.invoke(JSON.stringify({ action, payload }), (raw: string) => {
          const response = JSON.parse(raw);
          if (response.ok) resolve(response.result);
          else reject(new Error(response.error));
        }),
      ),
    subscribe: (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}
