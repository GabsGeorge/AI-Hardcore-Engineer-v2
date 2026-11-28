import { createBrowserRouter, type RouteObject } from "react-router-dom";
import { AppLayout } from "./layout";
import { AuthGuard, HomeRedirect, RoleGuard } from "./guards";
import { RouteError } from "./RouteError";

/**
 * Routes. Every page is loaded on demand (`lazy`), so each one is its own file in the build.
 *
 * Two error boundaries, so a page that throws or whose file fails to load never falls into
 * React Router's developer screen: the root one takes the whole window, and the one on the
 * signed-in area keeps the header and the navigation in place.
 */
export const routes: RouteObject[] = [
  {
    errorElement: <RouteError fullPage />,
    children: [
      { path: "/", element: <HomeRedirect /> },
      {
        path: "/login",
        lazy: async () => ({ Component: (await import("@/features/auth/LoginPage")).LoginPage }),
      },
      {
        path: "/register",
        lazy: async () => ({
          Component: (await import("@/features/auth/RegisterPage")).RegisterPage,
        }),
      },
      {
        path: "/verify",
        lazy: async () => ({ Component: (await import("@/features/auth/VerifyPage")).VerifyPage }),
      },
      {
        path: "/forgot",
        lazy: async () => ({ Component: (await import("@/features/auth/ForgotPage")).ForgotPage }),
      },
      {
        path: "/reset",
        lazy: async () => ({ Component: (await import("@/features/auth/ResetPage")).ResetPage }),
      },
      {
        path: "/invite",
        lazy: async () => ({
          Component: (await import("@/features/auth/AcceptInvitePage")).AcceptInvitePage,
        }),
      },
      {
        element: <AppLayout />,
        children: [
          {
            element: <AuthGuard />,
            errorElement: <RouteError />,
            children: [
              {
                element: <RoleGuard allow={["merchant"]} />,
                children: [
                  {
                    path: "/chat",
                    lazy: async () => ({
                      Component: (await import("@/features/chat/ChatPage")).ChatPage,
                    }),
                  },
                  {
                    path: "/chat/:id",
                    lazy: async () => ({
                      Component: (await import("@/features/chat/ChatPage")).ChatPage,
                    }),
                  },
                  {
                    path: "/history",
                    lazy: async () => ({
                      Component: (await import("@/features/conversations/HistoryPage")).HistoryPage,
                    }),
                  },
                  {
                    path: "/privacy",
                    lazy: async () => ({
                      Component: (await import("@/features/privacy/PrivacyPage")).PrivacyPage,
                    }),
                  },
                ],
              },
              {
                element: <RoleGuard allow={["operator", "admin"]} />,
                children: [
                  {
                    path: "/queue",
                    lazy: async () => ({
                      Component: (await import("@/features/handoffs/QueuePage")).QueuePage,
                    }),
                  },
                  {
                    path: "/queue/:ticket_id",
                    lazy: async () => ({
                      Component: (await import("@/features/handoffs/TicketPage")).TicketPage,
                    }),
                  },
                ],
              },
              {
                element: <RoleGuard allow={["admin"]} />,
                children: [
                  {
                    path: "/admin",
                    lazy: async () => ({
                      Component: (await import("@/features/admin/AdminHome")).AdminHome,
                    }),
                  },
                  {
                    path: "/admin/accounts",
                    lazy: async () => ({
                      Component: (await import("@/features/admin/AccountsPage")).AccountsPage,
                    }),
                  },
                  {
                    path: "/admin/merchants",
                    lazy: async () => ({
                      Component: (await import("@/features/admin/MerchantLinkPage"))
                        .MerchantLinkPage,
                    }),
                  },
                  {
                    path: "/admin/queue",
                    lazy: async () => ({
                      Component: (await import("@/features/admin/QueueOverviewPage"))
                        .QueueOverviewPage,
                    }),
                  },
                  {
                    path: "/admin/audit",
                    lazy: async () => ({
                      Component: (await import("@/features/admin/AuditPage")).AuditPage,
                    }),
                  },
                ],
              },
            ],
          },
        ],
      },
      { path: "*", lazy: async () => ({ Component: (await import("./NotFound")).NotFound }) },
    ],
  },
];

export const router = createBrowserRouter(routes);
