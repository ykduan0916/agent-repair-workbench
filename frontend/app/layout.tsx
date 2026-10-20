import type { Metadata, Viewport } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import "./styles/foundation.css";
import "./styles/overview.css";
import "./styles/runs.css";
import "./styles/analysis.css";
import "./styles/repairs.css";
import "./styles/workbench.css";
import "./styles/environment.css";
import "./styles/memory.css";
import "./styles/landing.css";
import "./styles/docs.css";

export const metadata: Metadata = {
  title: { default: "Agent 修复台", template: "%s · Agent 修复台" },
  description: "定位 Agent 工具调用的契约错误，在隔离环境复现，并核对修复前后的行为。",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f7f8" },
    { media: "(prefers-color-scheme: dark)", color: "#0e0f11" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className={`${GeistSans.variable} ${GeistMono.variable}`} suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: `(function(){var t='system';try{var v=localStorage.getItem('awb-theme');if(v==='light'||v==='dark')t=v}catch(e){}var r=document.documentElement;r.dataset.themePreference=t;r.dataset.theme=t==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):t})()` }} /></head>
      <body>{children}</body>
    </html>
  );
}
