import java.awt.Color;
import java.awt.Font;
import java.awt.FontMetrics;
import java.awt.Graphics;
import java.awt.Graphics2D;
import java.awt.Dimension;
import java.awt.Point;
import java.awt.RenderingHints;
import java.awt.event.ActionEvent;
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

public final class TicTacToe extends JFrame {
    private static final long serialVersionUID = 1L;

    private static final int GRID_SIZE = 3;
    private static final int CELL_SIZE = 150;
    private static final int PADDING = 30;
    private static final int BOARD_SIZE = GRID_SIZE * CELL_SIZE + 2 * PADDING;

    private static final Color BOARD_COLOR = new Color(18, 22, 28);
    private static final Color GRID_COLOR = new Color(35, 42, 51);
    private static final Color X_COLOR = new Color(52, 199, 89);
    private static final Color O_COLOR = new Color(255, 69, 58);
    private static final Color TEXT_COLOR = new Color(235, 239, 244);
    private static final Font FONT = new Font("Arial", Font.BOLD, 36);

    private final GamePanel gamePanel = new GamePanel();
    private boolean playerXTurn;
    private String status;
    private boolean gameActive;

    public TicTacToe() {
        super("Tic Tac Toe - Java 8");
        setDefaultCloseOperation(WindowConstants.EXIT_ON_CLOSE);
        setResizable(false);
        setContentPane(gamePanel);

        installKeyBindings();

        playerXTurn = true;
        status = "Player X's Turn";
        gameActive = true;
        gamePanel.repaint();

        pack();
        setLocationRelativeTo(null);
        setVisible(true);
    }

    private void installKeyBindings() {
        InputMap inputMap = gamePanel.getInputMap(JPanel.WHEN_IN_FOCUSED_WINDOW);
        ActionMap actionMap = gamePanel.getActionMap();

        bindKey(inputMap, actionMap, "Q", "quit");
        bindKey(inputMap, actionMap, "W", "restart");
    }

    private void bindKey(InputMap inputMap, ActionMap actionMap, String key, String actionName) {
        inputMap.put(KeyStroke.getKeyStroke(key), actionName);
        actionMap.put(actionName, new AbstractAction() {
            private static final long serialVersionUID = 1L;

            @Override
            public void actionPerformed(ActionEvent event) {
                if ("restart".equals(actionName)) {
                    resetGame();
                } else if ("quit".equals(actionName)) {
                    System.exit(0);
                }
            }
        });
    }

    private void resetGame() {
        gameActive = true;
        playerXTurn = true;
        status = "Player X's Turn";
        gamePanel.repaint();
    }

    private boolean checkWin() {
        int[][] board = gamePanel.getBoard();

        // Check rows
        for (int row = 0; row < GRID_SIZE; row++) {
            if (board[row][0] != 0 && board[row][0] == board[row][1] && board[row][1] == board[row][2]) {
                return board[row][0] == 1; // 1 = X wins
            }
        }

        // Check columns
        for (int col = 0; col < GRID_SIZE; col++) {
            if (board[0][col] != 0 && board[0][col] == board[1][col] && board[1][col] == board[2][col]) {
                return board[0][col] == 1;
            }
        }

        // Check diagonals
        if (board[0][0] != 0 && board[0][0] == board[1][1] && board[1][1] == board[2][2]) {
            return board[0][0] == 1;
        }
        if (board[0][2] != 0 && board[0][2] == board[1][1] && board[1][1] == board[2][0]) {
            return board[0][2] == 1;
        }

        return false;
    }

    private boolean checkDraw() {
        for (int row = 0; row < GRID_SIZE; row++) {
            for (int col = 0; col < GRID_SIZE; col++) {
                if (gamePanel.getBoard()[row][col] == 0) {
                    return false;
                }
            }
        }
        return true;
    }

    private void updateStatus() {
        if (!gameActive) {
            status = "Game Over";
            gamePanel.repaint();
        }
    }

    private class GamePanel extends JPanel {
        private int[][] board = new int[GRID_SIZE][GRID_SIZE];

        public GamePanel() {
            setPreferredSize(new Dimension(BOARD_SIZE, BOARD_SIZE + 60));
            setBackground(BOARD_COLOR);
            setFont(FONT);
        }

        public int[][] getBoard() {
            return board;
        }

        @Override
        protected void paintComponent(Graphics g) {
            super.paintComponent(g);
            Graphics2D g2d = (Graphics2D) g;
            g2d.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);

            g2d.setColor(BOARD_COLOR);
            g2d.fillRect(0, 0, BOARD_SIZE, BOARD_SIZE);

            g2d.setColor(GRID_COLOR);
            for (int i = 0; i < GRID_SIZE; i++) {
                g2d.drawLine(PADDING + i * CELL_SIZE, PADDING, PADDING + i * CELL_SIZE, BOARD_SIZE - PADDING);
                g2d.drawLine(PADDING, PADDING + i * CELL_SIZE, BOARD_SIZE - PADDING, PADDING + i * CELL_SIZE);
            }

            FontMetrics fm = g2d.getFontMetrics(FONT);
            for (int row = 0; row < GRID_SIZE; row++) {
                for (int col = 0; col < GRID_SIZE; col++) {
                    if (board[row][col] != 0) {
                        int x = PADDING + col * CELL_SIZE + fm.stringWidth("X") / 2;
                        int y = PADDING + row * CELL_SIZE + fm.getAscent() / 2;
                        g2d.setColor(board[row][col] == 1 ? X_COLOR : O_COLOR);
                        g2d.drawString(board[row][col] == 1 ? "X" : "O", x, y + fm.getAscent());
                    }
                }
            }

            g2d.setColor(TEXT_COLOR);
            g2d.setFont(new Font("Arial", Font.PLAIN, 16));
            g2d.drawString(status, 10, BOARD_SIZE + 40);
        }
    }

    public static void main(String[] args) {
        SwingUtilities.invokeLater(() -> {
            try {
                UIManager.setLookAndFeel(UIManager.getSystemLookAndFeelClassName());
            } catch (Exception ignored) {}
            new TicTacToe();
        });
    }
}