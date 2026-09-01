(() => {
  'use strict';
  const VERSION = 'v1';
  const sections = new Set(['build', 'preview', 'library', 'live']);
  const filters = new Set(['all', 'starter', 'look', 'favorites', 'recent']);
  const $ = (selector) => document.querySelector(selector);
  const defaultContext = () => ({section:'build', filter:'all', query:'', reference:null});
  let context = defaultContext();
  let applying = false;
  let projection = 0;
  let pending = null;

  function normalizeSearch(value) {
    const normalized=value.trim().replace(/\s+/g, ' ');
    return normalized.length <= 80 && !/[\u0000-\u001f\u007f]/.test(normalized) ? normalized : null;
  }
  function strictQuery(url) {
    const raw=url.search.slice(1); if (!raw) return true;
    try { raw.split('&').forEach((part) => part.split('=').forEach((value) => decodeURIComponent(value.replace(/\+/g, ' ')))); return true; } catch (_) { return false; }
  }
  function parseContext() {
    const url=new URL(window.location.href); const params=url.searchParams;
    const keys=[...params.keys()]; const allowed=new Set(['composer', 'section', 'filter', 'q', 'ref']);
    if (!strictQuery(url) || url.hash || keys.some((key) => !allowed.has(key)) || [...new Set(keys)].some((key) => params.getAll(key).length !== 1)) return {invalid:true};
    if (!keys.length) return {context:defaultContext()};
    if (params.get('composer') !== VERSION) return {invalid:true};
    const section=params.get('section') || 'build'; const filter=params.get('filter') || 'all'; const query=normalizeSearch(params.get('q') || ''); const encoded=params.get('ref');
    if (!sections.has(section) || !filters.has(filter) || query === null) return {invalid:true};
    let reference=null;
    if (encoded) {
      const parts=encoded.split(':');
      if (parts.length !== 2 || !['starter', 'look'].includes(parts[0]) || !/^[A-Za-z0-9_-]{1,80}$/.test(parts[1])) return {invalid:true};
      reference={kind:parts[0], id:parts[1]};
    }
    return {context:{section, filter, query, reference}};
  }
  function canonicalUrl(value) {
    const url=new URL(window.location.href); const params=new URLSearchParams();
    params.set('composer', VERSION); params.set('section', value.section);
    if (value.filter !== 'all') params.set('filter', value.filter);
    if (value.query) params.set('q', value.query);
    if (value.reference) params.set('ref', `${value.reference.kind}:${value.reference.id}`);
    url.search=params.toString(); url.hash=''; return url;
  }
  function sameReference(left, right) { return Boolean(left && right && left.kind === right.kind && left.id === right.id); }
  function sameContext(left, right) { return left.section === right.section && left.filter === right.filter && left.query === right.query && (sameReference(left.reference, right.reference) || (!left.reference && !right.reference)); }
  function itemMatches(item, value) {
    if (value.filter === 'starter' && item.kind !== 'starter') return false;
    if (value.filter === 'look' && item.kind !== 'look') return false;
    if (value.filter === 'favorites' && !item.favorite) return false;
    if (value.filter === 'recent' && !item.recent) return false;
    return item.name.toLocaleLowerCase().includes(value.query.toLocaleLowerCase());
  }
  function message(value='') { $('#composerNavigationMessage').textContent=value; }
  function focusContext(value) {
    requestAnimationFrame(() => {
      const target=value.reference
        ? [...document.querySelectorAll('.library-item')].find((item) => item.dataset.libraryKind === value.reference.kind && item.dataset.libraryId === value.reference.id)
        : $(`#${value.section}`);
      if (target) { target.scrollIntoView({block:'nearest', inline:'nearest'}); target.focus({preventScroll:true}); }
    });
  }
  function apply(value, ticket) {
    if (ticket !== projection) return;
    const snapshot=window.__composerLibraryNavigation && window.__composerLibraryNavigation.snapshot();
    if (!snapshot) return;
    if (value.reference && !snapshot.ready) { pending={value, ticket}; return; }
    const selected=value.reference && snapshot.items.find((item) => sameReference(item, value.reference));
    if (value.reference && (!selected || !itemMatches(selected, value))) return fail('This local Composer link is unavailable here. Returned to Build.', ticket);
    applying=true; window.__composerLibraryNavigation.apply({filter:value.filter, query:value.query, selection:selected && {kind:selected.kind, id:selected.id}}); applying=false;
    context=value; pending=null; message(selected && selected.kind === 'look' ? 'Selected saved look is local to this installation.' : ''); focusContext(value);
  }
  function fail(text, ticket=++projection) {
    if (ticket !== projection) return;
    const fallback=defaultContext(); history.replaceState(null, '', canonicalUrl(fallback)); apply(fallback, ticket); message(text);
  }
  function projectLocation() {
    const ticket=++projection; const parsed=parseContext();
    if (parsed.invalid) return fail('This local Composer link is invalid. Returned to Build.', ticket);
    message(''); apply(parsed.context, ticket);
    if (canonicalUrl(parsed.context).href !== window.location.href) history.replaceState(null, '', canonicalUrl(parsed.context));
  }
  function navigate(next, mode) {
    const value={...context, ...next}; const ticket=++projection;
    if (mode === 'push') history.pushState(null, '', canonicalUrl(value)); else history.replaceState(null, '', canonicalUrl(value));
    message(''); apply(value, ticket);
  }
  function copyLink() {
    const url=canonicalUrl(context).href;
    const copied=navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(url) : Promise.reject();
    const localNote=context.reference && context.reference.kind === 'look' ? 'Saved look links are local to this installation. ' : '';
    copied.then(() => message(`${localNote}Canonical local Composer link copied.`)).catch(() => message(`${localNote}Canonical local Composer link: ${url}`));
  }

  window.addEventListener('composer-library-navigation-change', (event) => {
    if (applying) return;
    if (pending) return apply(pending.value, pending.ticket);
    const snapshot=event.detail; const query=normalizeSearch(snapshot.query); const next={...context, filter:snapshot.filter, query:query === null ? '' : query, reference:snapshot.selection};
    if (!sameContext(next, context)) navigate(next, 'replace');
  });
  window.addEventListener('composer-library-card-select', (event) => navigate({section:'library', reference:event.detail.reference}, 'push'));
  window.addEventListener('popstate', projectLocation);
  document.querySelectorAll('[data-composer-section]').forEach((link) => link.addEventListener('click', (event) => { event.preventDefault(); navigate({section:link.dataset.composerSection, reference:null}, 'push'); }));
  $('#copyComposerLink').addEventListener('click', copyLink);
  projectLocation();
})();
