import java.sql.Connection;
import java.sql.DriverManager;

/** Waits for the database and creates the pilot's schema (Hibernate creates the tables in it). */
public class CreateSchema {
    public static void main(String[] a) throws Exception {
        if (!a[3].matches("[A-Za-z_][A-Za-z0-9_]*")) throw new IllegalArgumentException("bad schema name " + a[3]);
        for (int attempt = 1; ; attempt++) {
            try (Connection c = DriverManager.getConnection(a[0], a[1], a[2])) {
                c.createStatement().execute("CREATE SCHEMA IF NOT EXISTS " + a[3]);
                return;
            } catch (Exception e) {
                if (attempt == 60) throw e;
                System.err.println("waiting for the database: " + e.getMessage());
                Thread.sleep(2000);
            }
        }
    }
}
