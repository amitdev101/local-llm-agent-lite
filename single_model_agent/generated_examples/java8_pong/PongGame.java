import javax.swing.*;
import java.awt.*;
import java.awt.event.*;

public class PongGame extends JPanel implements ActionListener {
    private static final int WIDTH = 400;
    private static final int HEIGHT = 600;
    private static final int PADDLE_WIDTH = 10;
    private static final int PADDLE_HEIGHT = 80;
    private static final int BALL_SIZE = 10;
    
    private int ballX, ballY;
    private int ballDX, ballDY;
    private int paddle1Y;
    private int paddle2Y;
    private int score1 = 0;
    private int score2 = 0;
    private boolean gameRunning = true;
    
    private Timer gameLoop;
    
    public PongGame() {
        setPreferredSize(new Dimension(WIDTH, HEIGHT));
        setBackground(Color.BLACK);
        setFocusable(true);
        addKeyListener(new KeyAdapter() {
            public void keyPressed(KeyEvent e) {
                if (e.getKeyCode() == KeyEvent.VK_UP) {
                    paddle1Y = Math.max(0, paddle1Y - 20);
                } else if (e.getKeyCode() == KeyEvent.VK_DOWN) {
                    paddle1Y = Math.min(HEIGHT - PADDLE_HEIGHT, paddle1Y + 20);
                }
            }
        });
        
        resetBall();
        gameLoop = new Timer(16, this);
        gameLoop.start();
    }
    
    private void resetBall() {
        ballX = WIDTH / 2;
        ballY = HEIGHT / 2;
        ballDX = (Math.random() > 0.5 ? 2 : -2);
        ballDY = (Math.random() > 0.5 ? 2 : -2);
    }
    
    @Override
    public void paintComponent(Graphics g) {
        super.paintComponent(g);
        g.setColor(Color.WHITE);
        
        // Draw paddles
        g.fillRect(0, paddle1Y, PADDLE_WIDTH, PADDLE_HEIGHT);
        g.fillRect(WIDTH - PADDLE_WIDTH, paddle2Y, PADDLE_WIDTH, PADDLE_HEIGHT);
        
        // Draw ball
        g.fillOval(ballX, ballY, BALL_SIZE, BALL_SIZE);
        
        // Draw score
        g.setFont(new Font("Arial", Font.BOLD, 40));
        g.drawString("" + score1, WIDTH / 2 - 40, 50);
        g.drawString("" + score2, WIDTH / 2 + 40, 50);
    }
    
    @Override
    public void actionPerformed(ActionEvent e) {
        if (!gameRunning) return;
        
        // Move ball
        ballX += ballDX;
        ballY += ballDY;
        
        // Bounce off top and bottom
        if (ballY <= 0 || ballY >= HEIGHT - BALL_SIZE) {
            ballDY = -ballDY;
        }
        
        // Check paddle collisions
        // Left paddle
        if (ballX <= PADDLE_WIDTH && 
            ballY >= paddle1Y && 
            ballY <= paddle1Y + PADDLE_HEIGHT) {
            ballDX = -ballDX;
            ballX = PADDLE_WIDTH + 1;
        }
        
        // Right paddle
        if (ballX >= WIDTH - PADDLE_WIDTH - BALL_SIZE && 
            ballY >= paddle2Y && 
            ballY <= paddle2Y + PADDLE_HEIGHT) {
            ballDX = -ballDX;
            ballX = WIDTH - PADDLE_WIDTH - BALL_SIZE - 1;
        }
        
        // Score
        if (ballX < 0) {
            score2++;
            resetBall();
        } else if (ballX > WIDTH) {
            score1++;
            resetBall();
        }
        
        // Update paddles to follow mouse
        paddle1Y = (int)(paddle1Y + (MouseY - paddle1Y) * 0.1);
        paddle2Y = (int)(paddle2Y + (MouseY - paddle2Y) * 0.1);
        
        repaint();
    }
    
    private int MouseY = HEIGHT / 2;
    
    public static void main(String[] args) {
        SwingUtilities.invokeLater(new Runnable() {
            @Override
            public void run() {
                JFrame frame = new JFrame("Pong");
                PongGame game = new PongGame();
                frame.add(game);
                frame.setSize(WIDTH, HEIGHT);
                frame.setDefaultCloseOperation(JFrame.EXIT_ON_CLOSE);
                frame.setVisible(true);
            }
        });
    }
}