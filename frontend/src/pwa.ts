import { api } from "./api";

export interface NotificationPreferences {
  campaign_finished: boolean;
  attention: boolean;
}

// --- service worker и установка ---------------------------------------------------------

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

let installPrompt: BeforeInstallPromptEvent | null = null;
const installListeners = new Set<() => void>();

export function registerServiceWorker() {
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
  // Android/Chrome: браузер сам предлагает установку, если её не перехватить.
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    installPrompt = e as BeforeInstallPromptEvent;
    installListeners.forEach((fn) => fn());
  });
  window.addEventListener("appinstalled", () => {
    installPrompt = null;
    installListeners.forEach((fn) => fn());
  });
}

export function canPromptInstall(): boolean {
  return installPrompt !== null;
}

export function onInstallAvailabilityChange(fn: () => void): () => void {
  installListeners.add(fn);
  return () => installListeners.delete(fn);
}

export async function promptInstall(): Promise<boolean> {
  if (!installPrompt) return false;
  await installPrompt.prompt();
  const { outcome } = await installPrompt.userChoice;
  installPrompt = null;
  installListeners.forEach((fn) => fn());
  return outcome === "accepted";
}

export function isStandalone(): boolean {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  );
}

export function isIOS(): boolean {
  return (
    /iPhone|iPad|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  );
}

// --- push-уведомления --------------------------------------------------------------------

export type PushSupport = "supported" | "insecure" | "needs-install" | "unsupported";

export function pushSupport(): PushSupport {
  if (!window.isSecureContext) return "insecure";
  // На iPhone push доступен только приложению, добавленному на экран «Домой» (iOS 16.4+).
  if (isIOS() && !isStandalone()) return "needs-install";
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    return "unsupported";
  }
  return "supported";
}

function base64UrlToBytes(value: string): Uint8Array {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (value.length % 4)) % 4);
  const raw = atob(padded);
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

function sameKey(sub: PushSubscription, publicKey: string): boolean {
  const current = sub.options.applicationServerKey;
  if (!current) return false;
  const a = new Uint8Array(current);
  const b = base64UrlToBytes(publicKey);
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

async function registration(): Promise<ServiceWorkerRegistration> {
  const existing = await navigator.serviceWorker.getRegistration();
  if (!existing) await navigator.serviceWorker.register("/sw.js");
  return Promise.race([
    navigator.serviceWorker.ready,
    new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error("Service worker не запустился — обновите страницу")), 10000),
    ),
  ]);
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  const reg = await registration();
  return reg.pushManager.getSubscription();
}

export async function enablePush(preferences: NotificationPreferences): Promise<PushSubscription> {
  // Разрешение нужно запрашивать сразу в обработчике нажатия, иначе iOS его не покажет.
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("Уведомления запрещены. Разрешите их в настройках браузера или телефона.");
  }
  const { public_key } = await api.get<{ public_key: string }>("/api/push/config");
  const reg = await registration();
  let sub = await reg.pushManager.getSubscription();
  if (sub && !sameKey(sub, public_key)) {
    await sub.unsubscribe();
    sub = null;
  }
  if (!sub) {
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: base64UrlToBytes(public_key),
    });
  }
  await savePreferences(sub, preferences);
  return sub;
}

export async function savePreferences(sub: PushSubscription, preferences: NotificationPreferences) {
  await api.post("/api/push/subscriptions", { ...sub.toJSON(), preferences });
}

export async function disablePush(sub: PushSubscription) {
  await api.post("/api/push/subscriptions/delete", { endpoint: sub.endpoint });
  await sub.unsubscribe();
}
