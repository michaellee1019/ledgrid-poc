/* Retire the old Composer offline cache for existing installed clients. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil((async () => {
  const names = await caches.keys();
  await Promise.all(names.filter((name) => name.startsWith('composer-shell-') || name.startsWith('ledgrid-composer-')).map((name) => caches.delete(name)));
  await self.registration.unregister();
  const clients = await self.clients.matchAll({type: 'window'});
  clients.forEach((client) => client.navigate(client.url));
})()));
