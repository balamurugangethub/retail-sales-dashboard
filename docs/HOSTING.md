# Keeping your data online (Vercel + Supabase)

The hosted app has no permanent disk, so `cloud.py` keeps a copy of each account's database in Supabase
and brings it back when a visitor returns. It uses only HTTPS (no database password in the app).

## One-time setup

1. In Supabase, run the SQL below (SQL editor), replacing `PUT_SHA256_OF_YOUR_TOKEN_HERE` with the
   SHA-256 of a long random token you generate (for example `python -c "import secrets,hashlib;t=secrets.token_urlsafe(32);print(t);print(hashlib.sha256(t.encode()).hexdigest())"`).
2. In Vercel, set these environment variables and redeploy:
   * `RSD_STORE_URL`: your Supabase project URL
   * `RSD_STORE_KEY`: the project's publishable (anon) key
   * `RSD_STORE_TOKEN`: the random token itself (not the hash)
   * `SECRET_KEY`: any long random string (signs the sign-in cookie)

With these set, the site lets people create accounts and upload their own data. Without them it stays a read-only demo.

## SQL

```sql
create schema if not exists rsd;
revoke all on schema rsd from public, anon, authenticated;
create table if not exists rsd.files (name text primary key, data bytea not null,
  version bigint not null default 1, updated timestamptz not null default now());
alter table rsd.files enable row level security;
revoke all on rsd.files from public, anon, authenticated;
create table if not exists rsd.config (key text primary key, value text not null);
alter table rsd.config enable row level security;
revoke all on rsd.config from public, anon, authenticated;
insert into rsd.config(key, value) values ('token_sha256', 'PUT_SHA256_OF_YOUR_TOKEN_HERE')
  on conflict (key) do update set value = excluded.value;

create or replace function public.rsd_check(p_token text) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if p_token is null or encode(sha256(convert_to(p_token, 'utf8')), 'hex')
       is distinct from (select value from rsd.config where key = 'token_sha256') then
    raise exception 'not allowed' using errcode = '28000';
  end if;
end $$;
create or replace function public.rsd_version(p_token text, p_name text) returns bigint
language plpgsql security definer set search_path = '' as $$
begin perform public.rsd_check(p_token); return (select version from rsd.files where name = p_name); end $$;
create or replace function public.rsd_get(p_token text, p_name text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare r record;
begin
  perform public.rsd_check(p_token);
  select version, data into r from rsd.files where name = p_name;
  if not found then return null; end if;
  return jsonb_build_object('version', r.version, 'data', encode(r.data, 'base64'));
end $$;
create or replace function public.rsd_put(p_token text, p_name text, p_data text) returns bigint
language plpgsql security definer set search_path = '' as $$
declare v bigint;
begin
  perform public.rsd_check(p_token);
  insert into rsd.files(name, data) values (p_name, decode(p_data, 'base64'))
  on conflict (name) do update set data = excluded.data, version = rsd.files.version + 1, updated = now()
  returning version into v;
  return v;
end $$;
create or replace function public.rsd_delete(p_token text, p_name text) returns void
language plpgsql security definer set search_path = '' as $$
begin perform public.rsd_check(p_token); delete from rsd.files where name = p_name; end $$;

revoke all on function public.rsd_check(text) from public, anon, authenticated;
revoke all on function public.rsd_version(text, text), public.rsd_get(text, text),
  public.rsd_put(text, text, text), public.rsd_delete(text, text) from public;
grant execute on function public.rsd_version(text, text), public.rsd_get(text, text),
  public.rsd_put(text, text, text), public.rsd_delete(text, text) to anon;
```

The `anon` role can call the four functions, but each one refuses unless it is given the secret token,
and the tables themselves are not reachable through the API.

Limits: two people using the same account at the same moment on different servers can overwrite each
other (last save wins). Each account's database is stored compressed; keep uploads under a few MB on Vercel
(its request limit is about 4.5 MB).
