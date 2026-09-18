# Java 8 Snake

A dependency-free Swing Snake game produced during the real single-model-agent validation and
repaired after independent review.

## Run

```powershell
javac SnakeGame.java
java SnakeGame
```

The source is verified with Corretto Java 8 (`javac 1.8.0_472`) using `-Xlint:all -source 8
-target 8`.

## Controls

- `Enter`: start or restart
- Arrow keys or `WASD`: steer

The game prevents direct reversal, accepts at most one direction change per tick, keeps food off
the snake, handles a completed board, and uses one Swing timer for start/restart cycles.
