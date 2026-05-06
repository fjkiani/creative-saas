import React, { useState, useCallback } from 'react'

interface Asset {
  product_id: string
  market: string
  aspect_ratio: string
  language: string
  storage_url: string
  storage_path: string
}

interface Props {
  assets: Asset[]
}

const RATIO_LABELS: Record<string, string> = {
  '1:1':  '1:1 — Feed',
  '9:16': '9:16 — Stories',
  '16:9': '16:9 — Display',
}

const RATIO_ASPECT: Record<string, string> = {
  '1:1':  '1 / 1',
  '9:16': '9 / 16',
  '16:9': '16 / 9',
}

/**
 * Download a file from any origin (including Supabase Storage CDN).
 *
 * The HTML `download` attribute only works for same-origin URLs.
 * For cross-origin URLs (e.g. https://xxx.supabase.co/...) the browser
 * ignores `download` and just navigates to the URL instead.
 *
 * Fix: fetch the bytes, create a local blob URL, click that — always works.
 */
async function downloadAsset(url: string, filename: string): Promise<void> {
  try {
    const res = await fetch(url)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const blob = await res.blob()
    const objectUrl = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = objectUrl
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    // Release the object URL after a short delay
    setTimeout(() => URL.revokeObjectURL(objectUrl), 5000)
  } catch (err) {
    // Fallback: open in new tab so user can save manually
    console.warn('Download failed, opening in new tab:', err)
    window.open(url, '_blank', 'noreferrer')
  }
}

function assetFilename(asset: Asset): string {
  const ratio = asset.aspect_ratio.replace(':', 'x')
  return `${asset.product_id}_${asset.market}_${ratio}_${asset.language}.png`
}

export function AssetGrid({ assets }: Props) {
  const [selectedProduct, setSelectedProduct] = useState<string | null>(null)
  const [selectedMarket, setSelectedMarket] = useState<string | null>(null)
  const [lightbox, setLightbox] = useState<Asset | null>(null)
  const [downloading, setDownloading] = useState<string | null>(null)

  const handleDownload = useCallback(async (asset: Asset) => {
    const key = `${asset.product_id}-${asset.market}-${asset.aspect_ratio}`
    setDownloading(key)
    await downloadAsset(asset.storage_url, assetFilename(asset))
    setDownloading(null)
  }, [])

  if (assets.length === 0) {
    return (
      <div style={styles.empty}>
        <div style={styles.emptyIcon}>🎨</div>
        <div style={styles.emptyText}>Assets will appear here as they're generated</div>
      </div>
    )
  }

  // Group by product × market
  const products = [...new Set(assets.map(a => a.product_id))]
  const markets = [...new Set(assets.map(a => a.market))]
  const ratios = ['1:1', '9:16', '16:9']

  const filteredProducts = selectedProduct ? [selectedProduct] : products
  const filteredMarkets = selectedMarket ? [selectedMarket] : markets

  return (
    <div style={styles.container}>
      {/* Filters */}
      <div style={styles.filters}>
        <div style={styles.filterGroup}>
          <span style={styles.filterLabel}>Product</span>
          <button
            style={{ ...styles.chip, ...(selectedProduct === null ? styles.chipActive : {}) }}
            onClick={() => setSelectedProduct(null)}
          >All</button>
          {products.map(p => (
            <button
              key={p}
              style={{ ...styles.chip, ...(selectedProduct === p ? styles.chipActive : {}) }}
              onClick={() => setSelectedProduct(p === selectedProduct ? null : p)}
            >{p.replace(/_/g, ' ')}</button>
          ))}
        </div>
        <div style={styles.filterGroup}>
          <span style={styles.filterLabel}>Market</span>
          <button
            style={{ ...styles.chip, ...(selectedMarket === null ? styles.chipActive : {}) }}
            onClick={() => setSelectedMarket(null)}
          >All</button>
          {markets.map(m => (
            <button
              key={m}
              style={{ ...styles.chip, ...(selectedMarket === m ? styles.chipActive : {}) }}
              onClick={() => setSelectedMarket(m === selectedMarket ? null : m)}
            >{m}</button>
          ))}
        </div>
      </div>

      {/* Asset grid — grouped by product × market */}
      {filteredProducts.map(product => (
        filteredMarkets.map(market => {
          const productAssets = assets.filter(
            a => a.product_id === product && a.market === market
          )
          if (productAssets.length === 0) return null

          return (
            <div key={`${product}-${market}`} style={styles.group}>
              <div style={styles.groupHeader}>
                <span style={styles.groupProduct}>{product.replace(/_/g, ' ')}</span>
                <span style={styles.groupSep}>×</span>
                <span style={styles.groupMarket}>{market}</span>
                <span style={styles.groupLang}>
                  {productAssets[0]?.language?.toUpperCase()}
                </span>
              </div>

              <div style={styles.ratioRow}>
                {ratios.map(ratio => {
                  const asset = productAssets.find(a => a.aspect_ratio === ratio)
                  const dlKey = asset ? `${asset.product_id}-${asset.market}-${asset.aspect_ratio}` : null
                  const isDownloading = dlKey !== null && downloading === dlKey

                  return (
                    <div key={ratio} style={styles.ratioCell}>
                      <div style={styles.ratioLabel}>{RATIO_LABELS[ratio] || ratio}</div>
                      {asset ? (
                        <div style={{ position: 'relative' }}>
                          <div
                            style={{
                              ...styles.imageWrapper,
                              aspectRatio: RATIO_ASPECT[ratio] || '1/1',
                              cursor: 'pointer',
                            }}
                            onClick={() => setLightbox(asset)}
                          >
                            <img
                              src={asset.storage_url}
                              alt={`${product} ${market} ${ratio}`}
                              style={styles.image}
                              loading="lazy"
                            />
                            <div style={styles.imageOverlay}>
                              <span style={styles.expandIcon}>⤢</span>
                            </div>
                          </div>
                          {/* Per-card download button */}
                          <button
                            style={{
                              ...styles.cardDownloadBtn,
                              opacity: isDownloading ? 0.5 : 1,
                              cursor: isDownloading ? 'wait' : 'pointer',
                            }}
                            onClick={() => handleDownload(asset)}
                            disabled={isDownloading}
                            title={`Download ${ratio}`}
                          >
                            {isDownloading ? '...' : '↓'}
                          </button>
                        </div>
                      ) : (
                        <div style={{
                          ...styles.placeholder,
                          aspectRatio: RATIO_ASPECT[ratio] || '1/1',
                        }}>
                          <span style={styles.placeholderText}>Generating...</span>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })
      ))}

      {/* Lightbox */}
      {lightbox && (
        <div style={styles.lightboxOverlay} onClick={() => setLightbox(null)}>
          <div style={styles.lightboxContent} onClick={e => e.stopPropagation()}>
            <button style={styles.lightboxClose} onClick={() => setLightbox(null)}>✕</button>
            <img src={lightbox.storage_url} alt="Preview" style={styles.lightboxImage} />
            <div style={styles.lightboxMeta}>
              <span>{lightbox.product_id.replace(/_/g, ' ')}</span>
              <span>·</span>
              <span>{lightbox.market}</span>
              <span>·</span>
              <span>{lightbox.aspect_ratio}</span>
              <span>·</span>
              <span>{lightbox.language.toUpperCase()}</span>
            </div>
            {/* Cross-origin safe download — fetch blob then trigger save */}
            <button
              style={styles.downloadBtn}
              onClick={() => handleDownload(lightbox)}
            >
              {downloading === `${lightbox.product_id}-${lightbox.market}-${lightbox.aspect_ratio}`
                ? 'Downloading...'
                : 'Download'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: 'flex', flexDirection: 'column', gap: 24 },
  empty: {
    display: 'flex', flexDirection: 'column', alignItems: 'center',
    justifyContent: 'center', padding: 60, gap: 12,
    background: '#1a1a1a', borderRadius: 12, border: '1px dashed #333',
  },
  emptyIcon: { fontSize: 32 },
  emptyText: { color: '#555', fontSize: 14 },
  filters: { display: 'flex', gap: 16, flexWrap: 'wrap' },
  filterGroup: { display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' },
  filterLabel: { fontSize: 11, color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em', marginRight: 4 },
  chip: {
    fontSize: 12, padding: '4px 10px', borderRadius: 20,
    border: '1px solid #333', background: 'transparent', color: '#888',
    cursor: 'pointer', transition: 'all 0.15s',
  },
  chipActive: { background: '#1d4ed8', borderColor: '#1d4ed8', color: '#fff' },
  group: {
    background: '#1a1a1a', border: '1px solid #2a2a2a',
    borderRadius: 12, padding: 20,
  },
  groupHeader: {
    display: 'flex', alignItems: 'center', gap: 8,
    marginBottom: 16, flexWrap: 'wrap',
  },
  groupProduct: { fontSize: 14, fontWeight: 600, color: '#e8e8e8', textTransform: 'capitalize' },
  groupSep: { color: '#444', fontSize: 14 },
  groupMarket: { fontSize: 14, color: '#888' },
  groupLang: {
    fontSize: 10, fontWeight: 700, padding: '2px 6px',
    background: '#1d4ed8', color: '#fff', borderRadius: 4,
    letterSpacing: '0.05em',
  },
  ratioRow: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 },
  ratioCell: { display: 'flex', flexDirection: 'column', gap: 6 },
  ratioLabel: { fontSize: 11, color: '#666', textAlign: 'center' },
  imageWrapper: {
    position: 'relative', overflow: 'hidden',
    borderRadius: 8, background: '#111',
    border: '1px solid #2a2a2a',
  },
  image: { width: '100%', height: '100%', objectFit: 'cover', display: 'block' },
  imageOverlay: {
    position: 'absolute', inset: 0, background: 'rgba(0,0,0,0)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    transition: 'background 0.2s',
  },
  expandIcon: { fontSize: 20, color: '#fff', opacity: 0 },
  cardDownloadBtn: {
    position: 'absolute',
    bottom: 6,
    right: 6,
    width: 26,
    height: 26,
    borderRadius: '50%',
    background: 'rgba(0,0,0,0.65)',
    border: '1px solid rgba(255,255,255,0.15)',
    color: '#fff',
    fontSize: 13,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    lineHeight: 1,
  } as React.CSSProperties,
  placeholder: {
    background: '#111', borderRadius: 8, border: '1px dashed #2a2a2a',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
  },
  placeholderText: { fontSize: 11, color: '#444' },
  lightboxOverlay: {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    zIndex: 1000, padding: 20,
  },
  lightboxContent: {
    background: '#1a1a1a', borderRadius: 16, padding: 24,
    maxWidth: 700, width: '100%', position: 'relative',
    display: 'flex', flexDirection: 'column', gap: 12,
  },
  lightboxClose: {
    position: 'absolute', top: 12, right: 12,
    background: '#333', border: 'none', color: '#fff',
    width: 28, height: 28, borderRadius: '50%', cursor: 'pointer', fontSize: 12,
  },
  lightboxImage: { width: '100%', borderRadius: 8, display: 'block' },
  lightboxMeta: {
    display: 'flex', gap: 8, fontSize: 12, color: '#888',
    flexWrap: 'wrap',
  },
  downloadBtn: {
    display: 'inline-block', padding: '8px 20px',
    background: '#1d4ed8', color: '#fff', borderRadius: 8,
    border: 'none', fontSize: 13, fontWeight: 500,
    textAlign: 'center', cursor: 'pointer', width: '100%',
  },
}
