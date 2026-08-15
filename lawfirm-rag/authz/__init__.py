"""Authorization and policy enforcement.

The database is the source of truth for authorization facts
(user_profiles.role / .access_level / .admin / .firm_wide and
matter_access grants). JWT claims provide identity only (auth.uid()).

Never derive an authorization decision from client-supplied role,
access level, tenant ID, or matter IDs.
"""