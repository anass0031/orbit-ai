"use client";

import { AlertTriangle, CheckCircle2, Info } from "lucide-react";
import React, { createContext, useCallback, useContext, useState } from "react";

type Tone = "success" | "error" | "info";
interface Toast { id: number; message: string; tone: Tone }

const ToastContext = createContext<(message: string, tone?: Tone) => void>(() => {});

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const notify = useCallback((message: string, tone: Tone = "success") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-2), { id, message, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "error" ? 6000 : 3400);
  }, []);
  return (
    <ToastContext.Provider value={notify}>
      {children}
      <div className="fixed bottom-5 left-1/2 -translate-x-1/2 z-[60] flex flex-col items-center gap-2 pointer-events-none">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`px-4 py-2.5 rounded-xl bg-orbit-card border shadow-2xl flex items-center gap-2.5 text-xs font-medium text-white max-w-xl ${
              t.tone === "error" ? "border-orbit-red/50" : "border-orbit-purple/50"
            }`}
          >
            {t.tone === "success" && <CheckCircle2 className="w-4 h-4 text-orbit-green shrink-0" />}
            {t.tone === "error" && <AlertTriangle className="w-4 h-4 text-orbit-redLight shrink-0" />}
            {t.tone === "info" && <Info className="w-4 h-4 text-orbit-cyan shrink-0" />}
            <span>{t.message}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
