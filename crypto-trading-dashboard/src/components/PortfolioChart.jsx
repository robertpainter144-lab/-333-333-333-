import { useEffect, useRef } from 'react';
import { createChart, LineSeries, createSeriesMarkers } from 'lightweight-charts';

const TRADE_MARKER = {
  BUY: { shape: 'arrowUp', color: '#3fb950' },
  SELL: { shape: 'arrowDown', color: '#f85149' },
  SWAP: { shape: 'square', color: '#d29922' },
};

export function PortfolioChart({ valueHistory = [], tradeHistory = [], startValue, currentValue, height = 200 }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const markersRef = useRef(null);

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
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: '#30363d',
        timeVisible: true,
        secondsVisible: true,
      },
    });

    const lineSeries = chart.addSeries(LineSeries, {
      color: '#58a6ff',
      lineWidth: 2,
      priceFormat: {
        type: 'price',
        precision: 2,
        minMove: 0.01,
      },
    });

    const seriesMarkers = createSeriesMarkers(lineSeries, []);
    markersRef.current = seriesMarkers;

    chartRef.current = chart;
    seriesRef.current = lineSeries;

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
      seriesRef.current = null;
      markersRef.current = null;
    };
  }, [height]);

  useEffect(() => {
    if (!seriesRef.current) return;
    let data = valueHistory.map(({ time, value }) => ({ time, value }));
    if (data.length === 1) {
      data = [data[0], { time: data[0].time + 1, value: data[0].value }];
    }
    if (data.length > 0) {
      seriesRef.current.setData(data);
    }
  }, [valueHistory]);

  useEffect(() => {
    if (!markersRef.current || !tradeHistory.length) return;
    const markers = tradeHistory.map((t) => {
      const config = TRADE_MARKER[t.signal] || TRADE_MARKER.SWAP;
      return {
        time: Math.floor(t.time / 1000),
        position: config.shape === 'arrowUp' ? 'belowBar' : 'aboveBar',
        shape: config.shape,
        color: config.color,
        text: t.signal,
      };
    });
    markersRef.current.setMarkers(markers);
  }, [tradeHistory]);

  if (valueHistory.length === 0) {
    return (
      <div className="portfolio-chart">
        <div className="portfolio-chart-header">
          <span className="portfolio-chart-title">Portfolio value (paper)</span>
          <span className="portfolio-chart-value">
            {startValue != null && `Start: $${startValue.toFixed(2)}`}
            {currentValue != null && ` · Now: $${currentValue.toFixed(2)}`}
          </span>
        </div>
        <div className="portfolio-chart-placeholder">
          Start paper trading to see the live chart
        </div>
      </div>
    );
  }

  return (
    <div className="portfolio-chart">
      <div className="portfolio-chart-header">
        <span className="portfolio-chart-title">Portfolio value (paper)</span>
        <span className="portfolio-chart-value">
          {startValue != null && `Start: $${startValue.toFixed(2)}`}
          {currentValue != null && ` · Now: $${currentValue.toFixed(2)}`}
          {tradeHistory.length > 0 && ` · ${tradeHistory.length} trades`}
        </span>
      </div>
      <div ref={containerRef} style={{ width: '100%', minHeight: height }} />
    </div>
  );
}
