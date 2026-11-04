"use client";

import {
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { SeriesPoint } from "@/lib/api";

export default function HypePriceChart({
  data,
  currency,
}: {
  data: SeriesPoint[];
  currency: string;
}) {
  return (
    <div className="h-80 w-full">
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="#262626" vertical={false} />
          <XAxis dataKey="date" tick={{ fill: "#a3a3a3", fontSize: 12 }} minTickGap={32} />
          <YAxis
            yAxisId="hype"
            domain={[-4, 4]}
            tick={{ fill: "#a3a3a3", fontSize: 12 }}
            label={{ value: "Hype (z)", angle: -90, position: "insideLeft", fill: "#a3a3a3" }}
          />
          <YAxis
            yAxisId="price"
            orientation="right"
            domain={["auto", "auto"]}
            tick={{ fill: "#a3a3a3", fontSize: 12 }}
            tickFormatter={(v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 0 })}
          />
          <Tooltip
            contentStyle={{ background: "#171717", border: "1px solid #404040" }}
            formatter={(value, name) =>
              typeof value === "number"
                ? [name === `Close (${currency})` ? value.toLocaleString() : value.toFixed(2), name]
                : [value, name]
            }
          />
          <Legend />
          <Line
            yAxisId="price"
            type="monotone"
            dataKey="close"
            name={`Close (${currency})`}
            stroke="#60a5fa"
            dot={false}
            connectNulls
          />
          <Line
            yAxisId="hype"
            type="monotone"
            dataKey="hype"
            name="Hype"
            stroke="#f472b6"
            dot={false}
            connectNulls
          />
          <Line
            yAxisId="hype"
            type="monotone"
            dataKey="divergence"
            name="Divergence"
            stroke="#facc15"
            strokeDasharray="4 3"
            dot={false}
            connectNulls
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
