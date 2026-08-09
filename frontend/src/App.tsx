import { useEffect, useState } from "react";
import Lookup from "./pages/Lookup";
import Tasks from "./pages/Tasks";
import Settings from "./pages/Settings";

type Route = "lookup" | "tasks" | "settings";

function readRoute(): Route {
  if (window.location.hash.startsWith("#tasks")) return "tasks";
  if (window.location.hash === "#settings") return "settings";
  return "lookup";
}

export default function App() {
  const [route, setRoute] = useState<Route>(readRoute);
  useEffect(() => {
    const onHash = () => setRoute(readRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  if (route === "tasks") return <Tasks />;
  if (route === "settings") return <Settings />;
  return <Lookup />;
}
