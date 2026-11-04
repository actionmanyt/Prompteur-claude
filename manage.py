"""Gestion des comptes, à lancer dans le conteneur :

    docker exec -it md-converter python manage.py users
    docker exec -it md-converter python manage.py adduser marie
    docker exec -it md-converter python manage.py adduser arthur --admin
    docker exec -it md-converter python manage.py passwd marie
    docker exec -it md-converter python manage.py deluser marie
"""
import getpass
import sys

from core import create_user, db, hash_pw, init_db


def ask_password() -> str:
    while True:
        pw = getpass.getpass("Mot de passe (8 caractères minimum) : ")
        if len(pw) < 8:
            print("Trop court.")
        elif pw != getpass.getpass("Encore une fois : "):
            print("Les deux mots de passe sont différents.")
        else:
            return pw


def main(args: list[str]):
    init_db()
    if not args or args[0] not in ("users", "adduser", "passwd", "deluser"):
        print(__doc__)
        return 1
    cmd = args[0]
    if cmd == "users":
        with db() as con:
            for r in con.execute("SELECT username, is_admin, created FROM users ORDER BY username"):
                print(f"{r['username']:<20} {'admin' if r['is_admin'] else 'utilisateur':<12} {r['created']}")
        return 0
    if len(args) < 2:
        print("Précisez le nom du compte.")
        return 1
    name = args[1]
    with db() as con:
        exists = con.execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()
    if cmd == "adduser":
        if exists:
            print("Ce compte existe déjà.")
            return 1
        create_user(name, ask_password(), is_admin="--admin" in args)
        print("Compte créé.")
    elif not exists:
        print("Compte introuvable.")
        return 1
    elif cmd == "passwd":
        with db() as con:
            con.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_pw(ask_password()), exists["id"]))
            con.execute("DELETE FROM sessions WHERE user_id=?", (exists["id"],))
        print("Mot de passe changé (sessions ouvertes fermées).")
    elif cmd == "deluser":
        with db() as con:
            con.execute("DELETE FROM users WHERE id=?", (exists["id"],))
        print("Compte supprimé.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
