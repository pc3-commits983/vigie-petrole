// Service worker de Vigie pétrole : rend l'application installable et utilisable hors ligne.
// - Fichiers du site et données : réseau d'abord (les données changent chaque heure), cache si hors ligne.
// - Bibliothèques et polices des CDN (versions figées) : cache d'abord, mises à jour en arrière-plan.

const CACHE = "vigie-v1";
const COQUILLE = [
  "./",
  "index.html",
  "manifest.webmanifest",
  "icon.svg",
  "icon-192.png",
  "icon-512.png",
  "apple-touch-icon.png",
  "geo/land.json",
  "geo/borders.json",
];
const CDN = ["https://cdnjs.cloudflare.com/", "https://fonts.googleapis.com/", "https://fonts.gstatic.com/"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(COQUILLE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((cles) => Promise.all(cles.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

async function reseauDabord(requete) {
  const cache = await caches.open(CACHE);
  try {
    const reponse = await fetch(requete, { cache: "no-cache" });
    if (reponse.ok) cache.put(requete, reponse.clone());
    return reponse;
  } catch (erreur) {
    const enCache = await cache.match(requete, { ignoreSearch: true });
    if (enCache) return enCache;
    if (requete.mode === "navigate") {
      const page = await cache.match("index.html");
      if (page) return page;
    }
    throw erreur;
  }
}

async function cacheDabord(requete) {
  const cache = await caches.open(CACHE);
  const enCache = await cache.match(requete);
  const maj = fetch(requete)
    .then((reponse) => {
      if (reponse.ok || reponse.type === "opaque") cache.put(requete, reponse.clone());
      return reponse;
    })
    .catch(() => enCache);
  return enCache || maj;
}

self.addEventListener("fetch", (e) => {
  const requete = e.request;
  if (requete.method !== "GET") return;
  const url = new URL(requete.url);
  if (url.origin === self.location.origin) {
    e.respondWith(reseauDabord(requete));
  } else if (CDN.some((prefixe) => requete.url.startsWith(prefixe))) {
    e.respondWith(cacheDabord(requete));
  }
});
