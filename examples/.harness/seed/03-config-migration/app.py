import configparser


def load_config(path="config.ini"):
    cp = configparser.ConfigParser()
    cp.read(path)
    return {
        "host": cp.get("server", "host", fallback="127.0.0.1"),
        "port": cp.getint("server", "port", fallback=8000),
        "db_url": cp.get("database", "url", fallback="sqlite:///app.db"),
        "debug": cp.getboolean("server", "debug", fallback=False),
    }


if __name__ == "__main__":
    print(load_config())
