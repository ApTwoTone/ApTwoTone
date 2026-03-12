"use client";

import { createContext, useContext, ReactNode } from "react";

interface Session {
  token: string;
  email: string;
  name: string;
  role: "owner" | "operator";
}

interface AuthContextType {
  session: Session | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<string | null>;
  logout: () => void;
}

const PERMANENT_SESSION: Session = {
  token: "local",
  email: "kai@zoar.com",
  name: "Kai",
  role: "operator",
};

const AuthContext = createContext<AuthContextType>({
  session: PERMANENT_SESSION,
  loading: false,
  login: async () => null,
  logout: () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  return (
    <AuthContext.Provider
      value={{
        session: PERMANENT_SESSION,
        loading: false,
        login: async () => null,
        logout: () => {},
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
