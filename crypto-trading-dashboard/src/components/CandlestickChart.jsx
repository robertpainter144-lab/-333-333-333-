import { useEffect, useRef, useState } from 'react';
import { createChart, CandlestickSeries, LineSeries, createSeriesMarkers, LineStyle } from 'lightweight-charts';

function buildZeroBaselineData(candles) {
  if (!candles || candles.length === 0) return [];
  const first = candles[0].time;
  const last = candles[candles.length - 1].time;
  if (first >= last) {
    return [
      { time: first, value: 0 },
      { time: first + 1, value: 0 },
    ];
  }
  return [
    { time: first, value: 0 },
    { time: last, value: 0 },
  ];
}

function formatElapsed(seconds) {
  if (seconds == null || seconds < 0) return '';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  return hrs < 24 ? `${hrs}h` : `${Math.floor(hrs / 24)}d`;
}

function toTimestamp(time) {
  if (typeof time === 'number') return time;
  if (time && typeof time === 'object' && '_internal_timestamp' in time) {
    return time._internal_timestamp;
  }
  return time;
}

const SIGNAL_MARKER = {
  BUY: { shape: 'arrowUp', color: '#3fb950' },
  SELL: { shape: 'arrowDown', color: '#f85149' },
  HOLD: { shape: 'circle', color: '#8b949e' },
  SWAP: { shape: 'square', color: '#d29922' },
};

export function CandlestickChart({
  data,
  title,
  currentPrice,
  pctChangeSinceInception,
  inceptionTime,
  elapsedSeconds,
  isLoading,
  height = 280,
  decision,
  defaultMinimized = false,
}) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const candlestickRef = useRef(null);
  const zeroBaselineRef = useRef(null);
  const currentPriceLineRef = useRef(null);
  const markersRef = useRef(null);
  const [isMinimized, setIsMinimized] = useState(defaultMinimized);

  useEffect(() => {
    if (!containerRef.current) return;
    const width = Math.max(containerRef.current.clientWidth || 0, 200);

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: 'solid', color: '#0d1117' },
        textColor: '#c9d1d9',
      },
      grid: {
        vertLines: { color: '#21262d' },
        horzLines: { color: '#21262d' },
      },
      width,
      height,
      rightPriceScale: {
        borderColor: '#30363d',
        scaleMargins: { top: 0.1, bottom: 0.2 },
      },
      leftPriceScale: {
        visible: true,
        borderColor: '#30363d',
        scaleMargins: { top: 0.1, bottom: 0.2 },
        entireTextOnly: false,
      },
      timeScale: {
        borderColor: '#30363d',
        timeVisible: true,
        secondsVisible: true,
        tickMarkFormatter: (time, tickMarkType, locale) => {
          if (inceptionTime == null) return null;
          const t = toTimestamp(time);
          if (t == null) return null;
          const elapsed = t - inceptionTime;
          return formatElapsed(elapsed) || null;
        },
        tickMarkMaxCharacterLength: 10,
      },
      localization: {
        timeFormatter: (time) => {
          const t = toTimestamp(time);
          if (t == null) return '';
          const d = new Date(t * 1000);
          const clock = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
          if (inceptionTime != null) {
            const elapsed = t - inceptionTime;
            return `${clock} (${formatElapsed(elapsed)} ago)`;
          }
          return clock;
        },
        percentageFormatter: (value) => value.toFixed(6) + '%',
      },
    });

    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#3fb950',
      downColor: '#f85149',
      borderUpColor: '#3fb950',
      borderDownColor: '#f85149',
      priceScaleId: 'right',
      lastValueVisible: false,
      priceLineVisible: false,
      priceFormat: {
        type: 'price',
        precision: 4,
        minMove: 0.0001,
      },
    });

    const seriesMarkers = createSeriesMarkers(candlestickSeries, []);
    markersRef.current = seriesMarkers;

    const zeroBaselineSeries = chart.addSeries(LineSeries, {
      color: '#3fb950',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceScaleId: 'left',
      priceFormat: {
        type: 'custom',
        formatter: (value) => value.toFixed(2) + '%',
        tickmarksFormatter: (values) => values.map((v) => v.toFixed(2) + '%'),
        minMove: 0.01,
      },
      autoscaleInfoProvider: () => ({
        priceRange: { minValue: -1, maxValue: 1 },
      }),
    });

    if (data.length > 0) {
      candlestickSeries.setData(
        data.map((c) => ({
          time: c.time,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        }))
      );
      const zeroData = buildZeroBaselineData(data);
      if (zeroData.length > 0) {
        zeroBaselineSeries.setData(zeroData);
      }
    }

    chartRef.current = chart;
    candlestickRef.current = candlestickSeries;
    zeroBaselineRef.current = zeroBaselineSeries;

    const handleResize = () => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      markersRef.current?.detach?.();
      chart.remove();
      chartRef.current = null;
      candlestickRef.current = null;
      zeroBaselineRef.current = null;
      currentPriceLineRef.current = null;
      markersRef.current = null;
    };
  }, [height, inceptionTime]);

  useEffect(() => {
    if (candlestickRef.current && data && data.length > 0) {
      candlestickRef.current.setData(
        data.map((c) => ({
          time: c.time,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        }))
      );
    }
    if (zeroBaselineRef.current && data && data.length > 0) {
      const zeroData = buildZeroBaselineData(data);
      if (zeroData.length > 0) {
        zeroBaselineRef.current.setData(zeroData);
      }
    }
  }, [data]);

  useEffect(() => {
    if (isMinimized) return;
    if (containerRef.current && chartRef.current) {
      chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
    }
  }, [isMinimized]);

  useEffect(() => {
    if (!candlestickRef.current) return;
    if (currentPriceLineRef.current) {
      candlestickRef.current.removePriceLine(currentPriceLineRef.current);
      currentPriceLineRef.current = null;
    }
    if (currentPrice != null && typeof currentPrice === 'number') {
      const line = candlestickRef.current.createPriceLine({
        price: currentPrice,
        color: '#58a6ff',
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title: 'Current',
      });
      currentPriceLineRef.current = line;
    }
  }, [currentPrice]);

  useEffect(() => {
    if (!markersRef.current || !data?.length || !decision) return;
    const displaySignal = decision.displaySignal ?? decision.signal;
    const config = SIGNAL_MARKER[displaySignal];
    if (!config) return;
    const lastCandle = data[data.length - 1];
    const marker = {
      time: lastCandle.time,
      position: config.shape === 'arrowUp' ? 'belowBar' : config.shape === 'arrowDown' ? 'aboveBar' : 'aboveBar',
      shape: config.shape,
      color: config.color,
      text: displaySignal,
    };
    markersRef.current.setMarkers([marker]);
  }, [data, decision]);

  return (
    <div className={`chart-container ${isMinimized ? 'chart-minimized' : ''}`}>
      <div className="chart-header">
        <span className="chart-title">{title}</span>
        <div className="chart-header-right">
          {(elapsedSeconds != null || pctChangeSinceInception != null) && (
            <span className="chart-elapsed-pct">
              {elapsedSeconds != null && (
                <span className="chart-elapsed">{formatElapsed(elapsedSeconds)} elapsed</span>
              )}
              {elapsedSeconds != null && pctChangeSinceInception != null && (
                <span className="chart-separator"> · </span>
              )}
              {pctChangeSinceInception != null && (
                <span
                  className={`chart-pct-change ${
                    pctChangeSinceInception > 0 ? 'positive' : pctChangeSinceInception < 0 ? 'negative' : 'neutral'
                  }`}
                >
                  {pctChangeSinceInception >= 0 ? '+' : ''}
                  {pctChangeSinceInception.toFixed(2)}%
                </span>
              )}
            </span>
          )}
          {currentPrice != null && (
            <span className="chart-price">${currentPrice.toFixed(4)}</span>
          )}
          <button
            type="button"
            className="chart-toggle-btn"
            onClick={() => setIsMinimized((m) => !m)}
            title={isMinimized ? 'Expand chart' : 'Minimize chart'}
            aria-label={isMinimized ? 'Expand chart' : 'Minimize chart'}
          >
            {isMinimized ? '▼' : '▲'}
          </button>
        </div>
      </div>
      <div
        className="chart-body"
        style={{
          position: 'relative',
          overflow: 'hidden',
          maxHeight: isMinimized ? 0 : height + 50,
          opacity: isMinimized ? 0 : 1,
          transition: 'max-height 0.25s ease, opacity 0.2s ease',
        }}
      >
        <div ref={containerRef} style={{ width: '100%', minHeight: isMinimized ? 0 : height }} />
        {isLoading && data.length === 0 && (
          <div className="chart-loading">Loading…</div>
        )}
        {!isLoading && data.length === 0 && currentPrice == null && (
          <div className="chart-loading">Waiting for data…</div>
        )}
      </div>
    </div>
  );
}
