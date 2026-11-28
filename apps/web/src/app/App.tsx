import { Suspense, useEffect, useState } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import { LiveRegion, Spinner, ToastProvider } from "@/design";
import { initI18n } from "@/lib/i18n";
import { createQueryClient } from "./queryClient";
import { router } from "./routes";

const queryClient = createQueryClient();

export default function App() {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    void initI18n().then(() => setReady(true));
  }, []);
  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <LiveRegion>
          <Suspense fallback={<Spinner />}>
            <RouterProvider router={router} />
          </Suspense>
        </LiveRegion>
      </ToastProvider>
    </QueryClientProvider>
  );
}
