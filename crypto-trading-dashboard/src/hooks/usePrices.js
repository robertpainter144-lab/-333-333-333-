import { useState, useEffect, useRef, useCallback } from 'react';
import { ASSETS } from '../constants/contracts';

const POLL_INTERVAL_MS = 2000; // 2s for higher-frequency candlesticks

const COINPAPRIKA_URL = (id) =>
  `https://api.coinpaprika.com/v1/tickers/${id}?quotes=USD`;

const getPriceUrl = (id) =>
  import.meta.env.DEV
    ? `/api/prices/v1/tickers/${id}?quotes=USD`
    : COINPAPRIKA_URL(id);

export function usePrices(onPriceUpdate) {
  const [prices, setPrices] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const candleBuffersRef = useRef({});
  const onPriceUpdateRef = useRef(onPriceUpdate);
  onPriceUpdateRef.current = onPriceUpdate;

  const fetchPrices = useCallback(async () => {
    try {
      setError(null);
      const ids = [...new Set(ASSETS.map((a) => a.coinpaprikaId))];
      let results = await Promise.all(
        ids.map((id) =>
          fetch(getPriceUrl(id)).then((r) => (r.ok ? r.json() : null))
        )
      );

      const priceByCoin = {};
      ids.forEach((id, i) => {
        const data = results[i];
        if (data?.quotes?.USD?.price != null) {
          priceByCoin[id] = data.quotes.USD.price;
        }
      });

      let hasAnyPrice = Object.keys(priceByCoin).length > 0;
      if (!hasAnyPrice) {
        // Fallback: retry via CORS proxy in case proxy/direct failed
        const corsResults = await Promise.all(
          ids.map((id) =>
            fetch(
              `https://corsproxy.io/?${encodeURIComponent(COINPAPRIKA_URL(id))}`
            ).then((r) => (r.ok ? r.json() : null))
          )
        );
        corsResults.forEach((data, i) => {
          if (data?.quotes?.USD?.price != null) {
            priceByCoin[ids[i]] = data.quotes.USD.price;
          }
        });
        hasAnyPrice = Object.keys(priceByCoin).length > 0;
      }
      if (!hasAnyPrice) {
        setError('No price data received. Retrying…');
        return;
      }

      const newPrices = {};
      for (const asset of ASSETS) {
        let p = priceByCoin[asset.coinpaprikaId] ?? 0;
        if (p > 0 && asset.coinpaprikaId !== 'usdc-usd-coin') {
          const spread = p * 0.0003 * (Math.random() - 0.5) * 2;
          p = p + spread;
        }
        newPrices[asset.id] = p;
        if (p > 0) {
          if (!candleBuffersRef.current[asset.id]) {
            candleBuffersRef.current[asset.id] = [];
          }
          candleBuffersRef.current[asset.id].push({
            time: Math.floor(Date.now() / 1000),
            price: p,
          });
          if (candleBuffersRef.current[asset.id].length > 1440) {
            candleBuffersRef.current[asset.id].shift();
          }
        }
      }
      setPrices(newPrices);
      setLoading(false);
      onPriceUpdateRef.current?.(newPrices, candleBuffersRef.current);
    } catch (err) {
      setError(err.message || 'Failed to fetch prices');
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPrices();
    const interval = setInterval(fetchPrices, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchPrices]);

  return { prices, loading, error, retry: fetchPrices, candleBuffers: candleBuffersRef.current };
}
