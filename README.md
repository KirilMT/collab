# collab

Installable collaborative file-locking runtime.

## Development install

```powershell
cd C:/Users/kmartineztamayo/PycharmProjects/collab
python -m pip install -e .
```

## CLI

```powershell
collab active
collab daemon-start
collab daemon-stop
collab dashboard
```

## Notes

- This package is being extracted from mockCMMS .collab for behavior parity first.
- App repositories should consume this package and should not vendor collab runtime source.
