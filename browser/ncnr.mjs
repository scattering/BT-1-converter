// Fetch public NCNR acquisitions directly into browser memory. The local
// evidence endpoint receives metadata and summaries only, never input buffers.
export const DIRECTORY = 'https://ncnr.nist.gov/pub/ncnrdata/bt1/202610/Messegee/';
export const LISTING = `${DIRECTORY}?format=jsonld`;
const easternDate = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York', dateStyle: 'medium', timeStyle: 'long',
});

async function fetchDirect(url) {
  const response = await fetch(url, { mode: 'cors', credentials: 'omit', cache: 'no-store' });
  if (!response.ok) throw new Error(`NCNR request failed: HTTP ${response.status} (${url})`);
  return response;
}

async function latestListing() {
  const listing = await (await fetchDirect(LISTING)).json();
  if (!Array.isArray(listing.distribution)) throw new Error('NCNR returned an unrecognized directory listing.');
  const candidates = listing.distribution.filter(item => /^Mn5Si\d+\.bt1$/i.test(item.name || ''));
  for (const item of candidates) {
    const expectedURL = `${DIRECTORY}${encodeURIComponent(item.name)}`;
    if (item.contentUrl !== expectedURL || !/^[a-f\d]{64}$/i.test(item.sha256 || '') ||
        !Number.isSafeInteger(item.contentSize) || item.contentSize <= 0 ||
        !Number.isFinite(Date.parse(item.dateModified))) {
      throw new Error(`NCNR listing has invalid source metadata for ${item.name}.`);
    }
  }
  candidates.sort((a, b) => Date.parse(b.dateModified) - Date.parse(a.dateModified) || b.name.localeCompare(a.name));
  if (!candidates.length) throw new Error('No Mn5Si scans were found in the NCNR experiment directory.');
  return candidates;
}

async function verifiedDownload(source) {
  const bytes = await (await fetchDirect(source.contentUrl)).arrayBuffer();
  const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))]
    .map(value => value.toString(16).padStart(2, '0')).join('');
  return {
    file: { name: source.name, bytes },
    source: { ...source, downloaded_sha256: hash, downloaded_bytes: bytes.byteLength,
      verified: hash === source.sha256.toLowerCase() && bytes.byteLength === source.contentSize },
  };
}

export async function fetchLatestScans({ onStatus = () => {} } = {}) {
  onStatus('Loading the Mn5Si experiment directly from NCNR…');
  for (let attempt = 0; attempt < 2; attempt++) {
    const sources = await latestListing();
    const candidates = await Promise.all(sources.map(verifiedDownload));
    const mismatched = candidates.filter(item => !item.source.verified);
    if (!mismatched.length) return { items: candidates, listingURL: LISTING, listingRetries: attempt, fetchedAt: new Date().toISOString() };
    if (attempt === 1) throw new Error(`NCNR files changed during download (${mismatched.map(item => item.file.name).join(', ')}). A fresh-listing retry also changed; fetch again when the acquisition snapshot is stable.`);
    onStatus('A growing scan changed during download. Refreshing the listing and retrying once…');
  }
}

export function formatEasternDate(value) {
  if (!value || !Number.isFinite(Date.parse(value))) return value || 'Unknown';
  return easternDate.format(new Date(value));
}
