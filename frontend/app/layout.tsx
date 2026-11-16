import "./globals.css";
import React from "react";
import { Providers } from "./providers";

export const metadata = {
  title: "ORBIT AI — Data & AI Platform",
  description: "Import files, discover their schema and store them in the Bronze layer of the lakehouse.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="bg-orbit-bg text-orbit-text antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
