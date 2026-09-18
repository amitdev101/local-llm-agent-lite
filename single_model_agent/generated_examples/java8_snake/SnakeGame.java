import java.awt.Color;
import java.awt.Dimension;
import java.awt.Font;
import java.awt.FontMetrics;
import java.awt.Graphics;
import java.awt.Graphics2D;
import java.awt.Point;
import java.awt.RenderingHints;
import java.awt.event.ActionEvent;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import javax.swing.AbstractAction;
import javax.swing.ActionMap;
import javax.swing.InputMap;
import javax.swing.JFrame;
import javax.swing.JPanel;
import javax.swing.KeyStroke;
import javax.swing.SwingUtilities;
import javax.swing.Timer;
import javax.swing.UIManager;
import javax.swing.WindowConstants;

public final class SnakeGame extends JFrame {
    private static final long serialVersionUID = 1L;

    private static final int GRID_COLUMNS = 20;
    private static final int GRID_ROWS = 20;
    private static final int CELL_SIZE = 25;
    private static final int HUD_HEIGHT = 42;
    private static final int TICK_MILLIS = 110;

    private static final Color BOARD_COLOR = new Color(18, 22, 28);
    private static final Color GRID_COLOR = new Color(35, 42, 51);
    private static final Color SNAKE_COLOR = new Color(52, 199, 89);
    private static final Color HEAD_COLOR = new Color(116, 230, 140);
    private static final Color FOOD_COLOR = new Color(255, 69, 58);
    private static final Color TEXT_COLOR = new Color(235, 239, 244);

    private final List<Point> snake = new ArrayList<Point>();
    private final Random random = new Random();
    private final GamePanel gamePanel = new GamePanel();
    private final Timer timer;

    private Direction direction;
    private Direction queuedDirection;
    private Point food;
    private int score;
    private boolean running;
    private boolean gameOver;
    private boolean won;
    private boolean directionQueuedThisTick;

    private enum Direction {
        UP(0, -1), DOWN(0, 1), LEFT(-1, 0), RIGHT(1, 0);

        private final int dx;
        private final int dy;

        Direction(int dx, int dy) {
            this.dx = dx;
            this.dy = dy;
        }

        private boolean isOpposite(Direction other) {
            return dx + other.dx == 0 && dy + other.dy == 0;
        }
    }

    public SnakeGame() {
        super("Snake - Java 8");
        setDefaultCloseOperation(WindowConstants.EXIT_ON_CLOSE);
        setResizable(false);
        setContentPane(gamePanel);

        installKeyBindings();
        timer = new Timer(TICK_MILLIS, new AbstractAction() {
            private static final long serialVersionUID = 1L;

            @Override
            public void actionPerformed(ActionEvent event) {
                advanceGame();
            }
        });
        timer.setCoalesce(true);

        resetGame();
        pack();
        setLocationRelativeTo(null);
        setVisible(true);
    }

    private void installKeyBindings() {
        InputMap inputMap = gamePanel.getInputMap(JPanel.WHEN_IN_FOCUSED_WINDOW);
        ActionMap actionMap = gamePanel.getActionMap();

        bindDirection(inputMap, actionMap, "UP", Direction.UP);
        bindDirection(inputMap, actionMap, "W", Direction.UP);
        bindDirection(inputMap, actionMap, "DOWN", Direction.DOWN);
        bindDirection(inputMap, actionMap, "S", Direction.DOWN);
        bindDirection(inputMap, actionMap, "LEFT", Direction.LEFT);
        bindDirection(inputMap, actionMap, "A", Direction.LEFT);
        bindDirection(inputMap, actionMap, "RIGHT", Direction.RIGHT);
        bindDirection(inputMap, actionMap, "D", Direction.RIGHT);

        inputMap.put(KeyStroke.getKeyStroke("ENTER"), "startOrRestart");
        actionMap.put("startOrRestart", new AbstractAction() {
            private static final long serialVersionUID = 1L;

            @Override
            public void actionPerformed(ActionEvent event) {
                startOrRestart();
            }
        });
    }

    private void bindDirection(
            InputMap inputMap, ActionMap actionMap, String key, final Direction requested) {
        String actionName = "move" + requested.name() + key;
        inputMap.put(KeyStroke.getKeyStroke(key), actionName);
        actionMap.put(actionName, new AbstractAction() {
            private static final long serialVersionUID = 1L;

            @Override
            public void actionPerformed(ActionEvent event) {
                queueDirection(requested);
            }
        });
    }

    private void startOrRestart() {
        if (gameOver || won) {
            resetGame();
        }
        if (!running) {
            running = true;
            timer.start();
            gamePanel.repaint();
        }
    }

    private void queueDirection(Direction requested) {
        if (!running || gameOver || won || directionQueuedThisTick) {
            return;
        }
        if (!requested.isOpposite(direction)) {
            queuedDirection = requested;
            directionQueuedThisTick = true;
        }
    }

    private void resetGame() {
        if (timer != null) {
            timer.stop();
        }

        snake.clear();
        int startX = GRID_COLUMNS / 2;
        int startY = GRID_ROWS / 2;
        snake.add(new Point(startX, startY));
        snake.add(new Point(startX - 1, startY));
        snake.add(new Point(startX - 2, startY));

        direction = Direction.RIGHT;
        queuedDirection = direction;
        directionQueuedThisTick = false;
        score = 0;
        running = false;
        gameOver = false;
        won = false;
        placeFood();
        gamePanel.repaint();
    }

    private void advanceGame() {
        if (!running || gameOver || won) {
            return;
        }

        direction = queuedDirection;
        directionQueuedThisTick = false;

        Point head = snake.get(0);
        Point newHead = new Point(head.x + direction.dx, head.y + direction.dy);
        boolean eating = food != null && newHead.equals(food);

        if (outsideBoard(newHead) || hitsSnake(newHead, eating)) {
            finishGame(false);
            return;
        }

        snake.add(0, newHead);
        if (eating) {
            score++;
            if (!placeFood()) {
                finishGame(true);
                return;
            }
        } else {
            snake.remove(snake.size() - 1);
        }

        gamePanel.repaint();
    }

    private boolean outsideBoard(Point point) {
        return point.x < 0 || point.x >= GRID_COLUMNS || point.y < 0 || point.y >= GRID_ROWS;
    }

    private boolean hitsSnake(Point point, boolean eating) {
        // On a normal move the tail vacates its cell, so entering that one cell is legal.
        int collisionLength = eating ? snake.size() : snake.size() - 1;
        for (int index = 0; index < collisionLength; index++) {
            if (point.equals(snake.get(index))) {
                return true;
            }
        }
        return false;
    }

    private boolean placeFood() {
        List<Point> emptyCells = new ArrayList<Point>();
        for (int y = 0; y < GRID_ROWS; y++) {
            for (int x = 0; x < GRID_COLUMNS; x++) {
                Point candidate = new Point(x, y);
                if (!snake.contains(candidate)) {
                    emptyCells.add(candidate);
                }
            }
        }

        if (emptyCells.isEmpty()) {
            food = null;
            return false;
        }
        food = emptyCells.get(random.nextInt(emptyCells.size()));
        return true;
    }

    private void finishGame(boolean boardCompleted) {
        timer.stop();
        running = false;
        won = boardCompleted;
        gameOver = !boardCompleted;
        gamePanel.repaint();
    }

    private final class GamePanel extends JPanel {
        private static final long serialVersionUID = 1L;

        private GamePanel() {
            setPreferredSize(new Dimension(
                    GRID_COLUMNS * CELL_SIZE,
                    HUD_HEIGHT + GRID_ROWS * CELL_SIZE));
            setBackground(BOARD_COLOR);
            setDoubleBuffered(true);
        }

        @Override
        protected void paintComponent(Graphics graphics) {
            super.paintComponent(graphics);
            Graphics2D canvas = (Graphics2D) graphics.create();
            try {
                canvas.setRenderingHint(
                        RenderingHints.KEY_ANTIALIASING,
                        RenderingHints.VALUE_ANTIALIAS_ON);
                paintHud(canvas);
                paintBoard(canvas);
                paintFood(canvas);
                paintSnake(canvas);
                paintOverlay(canvas);
            } finally {
                canvas.dispose();
            }
        }

        private void paintHud(Graphics2D canvas) {
            canvas.setColor(new Color(24, 29, 36));
            canvas.fillRect(0, 0, getWidth(), HUD_HEIGHT);
            canvas.setColor(TEXT_COLOR);
            canvas.setFont(new Font(Font.SANS_SERIF, Font.BOLD, 18));
            canvas.drawString("Score: " + score, 12, 27);
            canvas.setFont(new Font(Font.SANS_SERIF, Font.PLAIN, 13));
            String help = "Arrows / WASD";
            FontMetrics metrics = canvas.getFontMetrics();
            canvas.drawString(help, getWidth() - metrics.stringWidth(help) - 12, 26);
        }

        private void paintBoard(Graphics2D canvas) {
            canvas.setColor(BOARD_COLOR);
            canvas.fillRect(0, HUD_HEIGHT, getWidth(), GRID_ROWS * CELL_SIZE);
            canvas.setColor(GRID_COLOR);
            for (int x = 0; x <= GRID_COLUMNS; x++) {
                int pixelX = x * CELL_SIZE;
                canvas.drawLine(pixelX, HUD_HEIGHT, pixelX, HUD_HEIGHT + GRID_ROWS * CELL_SIZE);
            }
            for (int y = 0; y <= GRID_ROWS; y++) {
                int pixelY = HUD_HEIGHT + y * CELL_SIZE;
                canvas.drawLine(0, pixelY, GRID_COLUMNS * CELL_SIZE, pixelY);
            }
        }

        private void paintFood(Graphics2D canvas) {
            if (food == null) {
                return;
            }
            int inset = 4;
            canvas.setColor(FOOD_COLOR);
            canvas.fillOval(
                    food.x * CELL_SIZE + inset,
                    HUD_HEIGHT + food.y * CELL_SIZE + inset,
                    CELL_SIZE - inset * 2,
                    CELL_SIZE - inset * 2);
        }

        private void paintSnake(Graphics2D canvas) {
            for (int index = snake.size() - 1; index >= 0; index--) {
                Point part = snake.get(index);
                canvas.setColor(index == 0 ? HEAD_COLOR : SNAKE_COLOR);
                canvas.fillRoundRect(
                        part.x * CELL_SIZE + 2,
                        HUD_HEIGHT + part.y * CELL_SIZE + 2,
                        CELL_SIZE - 4,
                        CELL_SIZE - 4,
                        8,
                        8);
            }
        }

        private void paintOverlay(Graphics2D canvas) {
            if (running) {
                return;
            }

            canvas.setColor(new Color(0, 0, 0, 165));
            canvas.fillRect(0, HUD_HEIGHT, getWidth(), GRID_ROWS * CELL_SIZE);
            String title;
            if (won) {
                title = "YOU WIN!";
            } else if (gameOver) {
                title = "GAME OVER";
            } else {
                title = "SNAKE";
            }
            drawCentered(canvas, title, HUD_HEIGHT + 220, new Font(Font.SANS_SERIF, Font.BOLD, 34));
            String instruction = gameOver || won
                    ? "Press ENTER to play again"
                    : "Press ENTER to start";
            drawCentered(canvas, instruction, HUD_HEIGHT + 260, new Font(Font.SANS_SERIF, Font.PLAIN, 17));
        }

        private void drawCentered(Graphics2D canvas, String text, int baseline, Font font) {
            canvas.setFont(font);
            canvas.setColor(TEXT_COLOR);
            FontMetrics metrics = canvas.getFontMetrics();
            canvas.drawString(text, (getWidth() - metrics.stringWidth(text)) / 2, baseline);
        }
    }

    public static void main(String[] arguments) {
        SwingUtilities.invokeLater(new Runnable() {
            @Override
            public void run() {
                try {
                    UIManager.setLookAndFeel(UIManager.getSystemLookAndFeelClassName());
                } catch (Exception ignored) {
                    // The game works with Swing's default look and feel.
                }
                new SnakeGame();
            }
        });
    }
}
