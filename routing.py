"""Decides which fictional department handles each kind of problem."""

DEPARTMENTS = {
    "network": {
        "name": "Network Operations",
        "handles": "Website addresses and server connections",
        "agents": ["Elin Berg", "Omar Haddad", "Sofia Lind"],
    },
    "security": {
        "name": "Security Operations",
        "handles": "Certificates and secure connections",
        "agents": ["Nadia Karimi", "Jonas Ek", "Maja Holm"],
    },
    "platform": {
        "name": "Platform Engineering",
        "handles": "Server errors and crashes",
        "agents": ["Leo Nyström", "Amina Yusuf", "Erik Sand"],
    },
    "web": {
        "name": "Web Applications",
        "handles": "Missing pages and broken redirects",
        "agents": ["Klara Dahl", "Daniel Roos", "Hanna Vik"],
    },
    "performance": {
        "name": "Performance & Reliability",
        "handles": "Slow responses",
        "agents": ["Ida Falk", "Samir Aziz", "Tove Strand"],
    },
}

# fault: department, ticket title, why it goes there, what the team will do
ROUTES = {
    "dns": ("network", "Website address can't be found",
            "Finding a website's address is handled by the network team.",
            "Check the domain's DNS records and registration."),
    "connection": ("network", "Server isn't accepting connections",
                   "Connections to servers are handled by the network team.",
                   "Check firewalls, load balancers and server availability."),
    "tls": ("security", "Secure connection is failing",
            "Certificates and encryption are handled by the security team.",
            "Renew or reinstall the certificate and verify the configuration."),
    "tls_expiring": ("security", "Security certificate expires soon",
                     "Certificates are handled by the security team.",
                     "Renew the certificate before it expires."),
    "blocked": ("security", "Website redirects somewhere unsafe",
                "Unsafe redirects are reviewed by the security team.",
                "Review where the redirect leads and why."),
    "server_error": ("platform", "Server is returning errors",
                     "Server errors and crashes are handled by the platform team.",
                     "Check server logs, recent releases and capacity."),
    "client_error": ("web", "Page can't be loaded",
                     "Missing pages and redirects are handled by the web team.",
                     "Restore the page or fix the link or redirect."),
    "slow": ("performance", "Website is responding slowly",
             "Slow responses are handled by the performance team.",
             "Find the slow part of the request and reduce the load."),
}


def route(fault: str) -> dict:
    department, title, reason, action = ROUTES[fault]
    return {"department": department, "title": title, "reason": reason, "action": action}


def choose_agent(department: str, open_counts: dict) -> str:
    """Pick the agent with the fewest open tickets, first listed on a tie."""
    agents = DEPARTMENTS[department]["agents"]
    return min(agents, key=lambda agent: open_counts.get(agent, 0))
